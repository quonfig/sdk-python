"""POSIX fork safety (qfg-lv4n.2).

Ports Reforge sdk-python's ``tests/test_forking.py`` (v1.2.2, "Fix fork safety
for singleton SDK" #26) to Quonfig's instance-based API. The design under test
is Reforge's, unchanged in principle:

* the hook is CHILD-ONLY (``os.register_at_fork(after_in_child=...)``) — the
  parent is never touched, before or after the syscall;
* the child drops every piece of inherited concurrency state, **including
  locks** (a ``threading.Lock`` held by a thread that did not survive the fork
  stays locked forever in the child), and rebuilds fresh;
* nothing inherited is ``join()``ed or ``close()``d — the threads do not exist
  in the child and the sockets belong to the parent.

The one forced difference from Reforge is the registry: Quonfig has no module
singleton to reset, so live ``Quonfig`` instances are tracked in a
``weakref.WeakSet`` and rebuilt in place.

Update delivery is exercised over a real in-process HTTP server with
``enable_sse=False``, so the Layer 2 fallback poller is the primary update
channel and no SSE fixture is needed. The server lives in the PARENT, so a
generation bump published after the fork is visible to the child only if the
child's own poller is alive.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

import pytest

from quonfig import Quonfig

requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fork with ONLY the client under test in the at-fork registry.

    In a real forking server, rebuilding every live client in the child is
    exactly right. In a full ``pytest`` run it drags in every client other test
    modules constructed and left running — including ones pointed at real
    hostnames — so the child would spend the test rebuilding those and firing
    their fetches. Swap the module-level ``WeakSet`` for an empty one so each
    test forks with a registry containing just its own client.

    Looked up through ``sys.modules`` rather than imported at the top of the
    file so this module still collects (and the behavioural assertions below
    still run, and fail) against a build that has no fork handling at all.
    """
    fork_module = sys.modules.get("quonfig._fork")
    if fork_module is not None:
        monkeypatch.setattr(fork_module, "_instances", weakref.WeakSet())


@pytest.fixture(autouse=True)
def _isolate_from_macos_fork_hazards(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the loopback HTTP path out of macOS's two fork-hostile system
    libraries, so these tests exercise the SDK's fork behavior and nothing
    else. Neither hazard involves the SDK, and neither exists on Linux — where
    the forking servers this covers actually run, and where CI runs.

    Tests marked ``no_macos_fork_isolation`` opt OUT: the whole point of the
    "a child that never calls the SDK does no work" tests is that the child
    never reaches either hazard, so neutering the hazards would make them
    vacuous. They run with the real ``getaddrinfo`` and no ``NO_PROXY``.

    1. ``_scproxy``. On macOS ``requests`` resolves proxy settings through
       ``urllib.request.proxy_bypass`` -> ``_scproxy`` -> SystemConfiguration,
       which enters the Objective-C runtime. If a request thread is inside an
       ObjC ``+initialize`` when ``fork()`` is called, libobjc deliberately
       aborts the child ("objc[...]: +[NSNumber initialize] may have been in
       progress in another thread when fork() was called ... Crashing
       instead"). ``NO_PROXY`` covering the loopback host makes ``requests``
       short-circuit before it ever calls ``proxy_bypass``.

    2. ``getaddrinfo``. macOS resolves through libinfo/mDNSResponder, which is
       not fork-safe: if any thread in the process holds the resolver's state
       at fork time — and a full ``pytest`` run always has some SDK thread
       resolving a hostname somewhere — the child segfaults the first time it
       resolves anything, loopback included. Answering numeric loopback
       addresses directly (which is what they are) keeps the child off that
       code path entirely.
    """
    if "no_macos_fork_isolation" in request.keywords:
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        return

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    real_getaddrinfo = socket.getaddrinfo

    def loopback_direct(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if host in ("127.0.0.1", "localhost", b"127.0.0.1"):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))
            ]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", loopback_direct)


# ----------------------------------------------------------------------
# In-process config server (lives in the parent; the child reaches it over
# the socket, so a post-fork publish is only visible to a live child poller)
# ----------------------------------------------------------------------


class _ConfigServer:
    """Serves ``GET /api/v2/configs``. ``generation`` and ``mode`` are mutated
    by the parent at will; ``mode="error"`` answers 503 so no fetch can
    succeed (and therefore no liveness stamp can advance)."""

    def __init__(self, configs: "Optional[list]" = None) -> None:
        self.generation = 1
        self.mode = "ok"
        # Config documents to serve. Empty by default — most tests only care
        # about the generation — but the fork tests that assert a child serves
        # a real VALUE need at least one.
        self.configs: list = configs or []
        # Config fetches that carried the SDK's own version header, i.e. ones
        # the SDK made. Lets a test tell SDK traffic apart from traffic a test
        # thread generates itself.
        self.sdk_requests = 0
        self._count_lock = threading.Lock()
        outer = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802
                if self.headers.get("X-Quonfig-SDK-Version"):
                    with outer._count_lock:
                        outer.sdk_requests += 1
                if outer.mode == "error":
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(
                    {
                        "configs": outer.configs,
                        "meta": {
                            "version": f"gen-{outer.generation}",
                            "environment": "Production",
                            "generation": outer.generation,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:  # silence the test server
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        _host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _string_config(key: str, value: str) -> dict:
    """A minimal always-matching string config document."""
    return {
        "id": key,
        "key": key,
        "configType": "config",
        "valueType": "string",
        "default": {"rules": [{"criteria": [], "value": {"type": "string", "value": value}}]},
    }


def _quonfig_threads() -> "list[str]":
    """Names of the SDK's own live threads in this process."""
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("quonfig"))


def _make_client(server: _ConfigServer, **overrides: Any) -> Quonfig:
    kwargs: dict = dict(
        sdk_key="test-backend-key",
        api_urls=[server.url],
        # SSE off -> the Layer 2 poller is the PRIMARY update channel and
        # engages right at init. No SSE fixture needed.
        enable_sse=False,
        fallback_poll_enabled=True,
        fallback_poll_interval_ms=100,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        init_timeout_ms=8000,
        on_init_failure="return_zero_value",
        hedge_delay_ms=500,
        config_fetch_hedge_abort_ms=2000,
    )
    kwargs.update(overrides)
    return Quonfig(**kwargs)


def _await(predicate: Callable[[], bool], within: float = 6.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _await_ready(client: Quonfig, within: float = 8.0) -> None:
    if not _await(client.ready, within):
        raise AssertionError("client did not become ready in time")


def _await_server_quiet(
    server: "_ConfigServer", quiet_for: float = 0.6, within: float = 10.0
) -> int:
    """Block until the server has seen no SDK request for ``quiet_for`` seconds,
    and return the count. Init fires two fetches (the initial hedge and the
    poller's engage-time tick) and a slow runner can land the second one late;
    without this, that straggler would be attributed to the forked child."""
    deadline = time.monotonic() + within
    last = server.sdk_requests
    quiet_since = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.05)
        current = server.sdk_requests
        if current != last:
            last = current
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since >= quiet_for:
            return current
    return server.sdk_requests


# ----------------------------------------------------------------------
# fork / pipe plumbing (Reforge's os.fork + os.pipe + os._exit(0) shape,
# with a timeout guard so a deadlocked child fails the test instead of
# hanging the suite)
# ----------------------------------------------------------------------


class _ChildPipe:
    """Newline-delimited messages from the child, read with a deadline."""

    def __init__(self, read_fd: int) -> None:
        self._fd = read_fd
        self._buf = b""

    def read_line(self, timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "<timeout>"
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready:
                return "<timeout>"
            chunk = os.read(self._fd, 65536)
            if not chunk:
                return "<eof>"
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode()

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


def _fork_child(body: Callable[[Callable[[str], None]], None]) -> "tuple[int, _ChildPipe]":
    """Fork and run ``body(send)`` in the child. ``send`` writes one
    newline-terminated message back to the parent. The child always
    ``os._exit(0)``s so no pytest teardown runs there."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # CHILD
        try:
            os.close(read_fd)

            def send(msg: str) -> None:
                os.write(write_fd, (msg + "\n").encode())

            try:
                body(send)
            except BaseException as e:  # noqa: BLE001 — report, never hang the parent
                try:
                    os.write(write_fd, f"error: {type(e).__name__}: {e}\n".encode())
                except OSError:
                    pass
        finally:
            try:
                os.close(write_fd)
            except OSError:
                pass
            os._exit(0)
    os.close(write_fd)
    return pid, _ChildPipe(read_fd)


def _reap(pid: int, timeout: float = 10.0) -> str:
    """Wait for the child, SIGKILLing it if it wedged (a deadlocked child must
    not hang the suite).

    Returns ``exit=N`` | ``signal=NAME`` | ``killed(timeout)`` — a child killed
    by a signal is how a macOS ``_scproxy`` / ``getaddrinfo`` fork crash shows
    up, and it must not be mistaken for a clean exit.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            if os.WIFSIGNALED(status):
                return f"signal={signal.Signals(os.WTERMSIG(status)).name}"
            return f"exit={os.WEXITSTATUS(status)}"
        time.sleep(0.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return "killed(timeout)"


class _LockHolder:
    """Holds one or more of the client's internal locks across the fork, on a
    thread that will NOT exist in the child. This is Reforge's test (a): the
    inherited lock is held forever in the child unless the SDK replaces it."""

    def __init__(self, locks: "list[Any]") -> None:
        self._locks = locks
        self._ready = threading.Event()
        self._release = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        acquired = []
        try:
            for lock in self._locks:
                lock.acquire()
                acquired.append(lock)
            self._ready.set()
            self._release.wait(timeout=60)
        finally:
            for lock in reversed(acquired):
                try:
                    lock.release()
                except RuntimeError:
                    pass

    def __enter__(self) -> "_LockHolder":
        self._thread.start()
        assert self._ready.wait(timeout=5), "lock holder thread never acquired the locks"
        return self

    def __exit__(self, *_exc: object) -> None:
        self._release.set()
        self._thread.join(timeout=5)


# ----------------------------------------------------------------------
# T1 — the child receives an update published AFTER the fork
# ----------------------------------------------------------------------


@requires_fork
def test_t1_child_receives_update_published_after_fork() -> None:
    """The whole point. On a client with no fork handling the child inherits a
    dead poller thread and goes permanently dark: it keeps serving the
    generation it was forked with and never sees anything published after."""
    server = _ConfigServer()
    client = _make_client(server)
    try:
        client.init()
        _await_ready(client)
        assert client.held_generation() == 1

        def child(send: Callable[[str], None]) -> None:
            saw = _await(lambda: client.held_generation() == 2, within=12.0)
            send(f"gen={client.held_generation()} saw_update={saw}")

        pid, pipe = _fork_child(child)
        try:
            # Publish AFTER the fork. Only a live child-side poller can see it.
            server.generation = 2
            message = pipe.read_line(timeout=20.0)
        finally:
            _reap(pid)
            pipe.close()

        assert message == "gen=2 saw_update=True", (
            f"child did not receive the post-fork update: {message!r}"
        )
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# T2 — the parent is untouched (regression guard for the sdk-ruby incident)
# ----------------------------------------------------------------------


@requires_fork
def test_t2_parent_is_untouched_by_the_fork() -> None:
    """The hook is child-only. The parent's poller thread, transport and store
    lock must be the SAME objects after the fork, the thread must still be
    alive, and the parent must keep receiving updates. This is the invariant
    sdk-ruby's parent-side teardown broke (qfg-lv4n)."""
    server = _ConfigServer()
    client = _make_client(server)
    try:
        client.init()
        _await_ready(client)
        assert client.fallback_poller_active()

        poller_before = client._fallback_poller
        assert poller_before is not None
        thread_before = poller_before._thread
        transport_before = client._transport
        store_lock_before = client._store._lock
        health_lock_before = client._health_lock
        refresh_before = client.last_successful_refresh()

        pid, pipe = _fork_child(lambda send: send("child-done"))
        try:
            assert client._fallback_poller is poller_before, "parent's poller was replaced"
            assert poller_before._thread is thread_before, "parent's poller thread was replaced"
            assert thread_before is not None and thread_before.is_alive(), (
                "parent's poller thread was torn down by the fork"
            )
            assert client._transport is transport_before, "parent's transport was replaced"
            assert client._store._lock is store_lock_before, "parent's store lock was replaced"
            assert client._health_lock is health_lock_before, "parent's health lock was replaced"
            assert client.last_successful_refresh() == refresh_before, (
                "parent's liveness stamp was reset by the fork"
            )

            # And the parent still receives updates published after the fork.
            server.generation = 2
            assert _await(lambda: client.held_generation() == 2, within=8.0), (
                "parent stopped receiving updates after the fork"
            )
            assert client.connection_state() == "connected"
            assert pipe.read_line(timeout=15.0) == "child-done"
        finally:
            _reap(pid)
            pipe.close()
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# T3 — an inherited HELD lock must not deadlock the child (Reforge test (a))
# ----------------------------------------------------------------------


@requires_fork
def test_t3_inherited_held_lock_does_not_deadlock_the_child() -> None:
    """A thread that does not survive the fork holds the client's store and
    health locks at fork time. In the child those locks can never be released,
    so evaluating or reading connection state deadlocks forever unless the SDK
    replaces them — exactly why Reforge replaces its module lock."""
    server = _ConfigServer()
    client = _make_client(server)
    try:
        client.init()
        _await_ready(client)

        # Acquire store -> health, the same order the install path takes, so
        # the parent cannot deadlock while the locks are held.
        with _LockHolder([client._store._lock, client._health_lock]):

            def child(send: Callable[[str], None]) -> None:
                value = client.get_string("no.such.key", default="fallback")
                state = client.connection_state()
                client.close()
                send(f"value={value} state={state} closed=True")

            pid, pipe = _fork_child(child)
            try:
                message = pipe.read_line(timeout=10.0)
            finally:
                _reap(pid)
                pipe.close()

        assert message.startswith("value=fallback"), (
            f"child deadlocked on an inherited held lock: {message!r}"
        )
        assert message.endswith("closed=True")
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# T4 — the child's telemetry buffers are fresh, not the parent's
# ----------------------------------------------------------------------


@requires_fork
def test_t4_child_telemetry_buffers_are_fresh() -> None:
    """Inherited telemetry is the parent's to flush. A child that keeps the
    inherited reporter re-POSTs the parent's buffered window (duplicate
    telemetry) and shares the parent's collector locks. dd-trace-rb discards
    inherited buffers in the child for the same reason."""
    server = _ConfigServer()
    client = _make_client(
        server,
        collect_evaluation_summaries=True,
        context_upload_mode="periodic_example",
        telemetry_url="http://127.0.0.1:1",
    )
    try:
        client.init()
        _await_ready(client)
        assert client._telemetry is not None, "test needs telemetry enabled"

        # Buffer a window in the PARENT and tag the reporter object so the
        # child can prove it is not holding the same one.
        client._telemetry.record_hedge_fired()
        client._telemetry.record_guard_rejected()
        client._telemetry.record_resolved_from(0)
        client._telemetry._fork_test_marker = "parent"  # type: ignore[attr-defined]

        def child(send: Callable[[str], None]) -> None:
            # The rebuild is lazy: touch the SDK so the child's own telemetry
            # reporter exists to be inspected.
            client.connection_state()
            telemetry = client._telemetry
            if telemetry is None:
                send("telemetry=None")
                return
            inherited = hasattr(telemetry, "_fork_test_marker")
            drained = telemetry._failover_collector.drain()
            send(f"inherited={inherited} buffered={drained is not None}")

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=15.0)
        finally:
            _reap(pid)
            pipe.close()

        assert message == "inherited=False buffered=False", (
            f"child inherited the parent's telemetry buffers: {message!r}"
        )
        # The parent still owns its own buffered window.
        assert client._telemetry._failover_collector.drain() is not None, (
            "the child's rebuild must not drain the parent's telemetry"
        )
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# T5 — close() returns promptly in the child
# ----------------------------------------------------------------------


@requires_fork
def test_t5_close_in_child_returns_promptly() -> None:
    """``close()`` takes the client's fallback lock. If that lock was held at
    fork time by a thread that does not exist in the child, ``close()`` blocks
    forever — the shutdown hang LaunchDarkly describes in ruby-server-sdk
    PR #430."""
    server = _ConfigServer()
    client = _make_client(server)
    try:
        client.init()
        _await_ready(client)

        with _LockHolder([client._fallback_lock]):

            def child(send: Callable[[str], None]) -> None:
                started = time.monotonic()
                client.close()
                send(f"closed_in={time.monotonic() - started:.3f}")

            pid, pipe = _fork_child(child)
            try:
                message = pipe.read_line(timeout=10.0)
            finally:
                _reap(pid)
                pipe.close()

        assert message.startswith("closed_in="), f"close() never returned in the child: {message!r}"
        elapsed = float(message.split("=", 1)[1])
        assert elapsed < 5.0, f"close() took {elapsed:.3f}s in the child"
    finally:
        client.close()
        server.close()


@requires_fork
def test_t5b_close_in_child_returns_promptly_with_no_rebuild() -> None:
    """``close()`` must also return promptly for an instance that was already
    closed before the fork — those stay closed, so no rebuild happens and
    there is nothing to tear down."""
    server = _ConfigServer()
    client = _make_client(server)
    try:
        client.init()
        _await_ready(client)
        client.close()

        def child(send: Callable[[str], None]) -> None:
            started = time.monotonic()
            client.close()
            send(
                f"closed_in={time.monotonic() - started:.3f} "
                f"poller_active={client.fallback_poller_active()}"
            )

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=10.0)
        finally:
            _reap(pid)
            pipe.close()

        assert message.startswith("closed_in="), f"close() never returned in the child: {message!r}"
        elapsed = float(message.split(" ", 1)[0].split("=", 1)[1])
        assert elapsed < 5.0, f"close() took {elapsed:.3f}s in the child"
        assert message.endswith("poller_active=False"), (
            f"a client closed before the fork must stay closed in the child: {message!r}"
        )
    finally:
        server.close()


# ----------------------------------------------------------------------
# T6 — connection_state() must not report the inherited "connected"
# ----------------------------------------------------------------------


@requires_fork
def test_t6_connection_state_in_child_is_not_inherited_connected() -> None:
    """The Cheddar Up failure mode: the SDK kept answering "connected" from
    state inherited across a fork while it was in fact receiving nothing.
    Immediately after the fork the child has confirmed nothing, so it must not
    claim to be connected until a fresh refresh actually succeeds."""
    server = _ConfigServer()
    client = _make_client(server)
    try:
        client.init()
        _await_ready(client)
        assert client.connection_state() == "connected"

        # Make every fetch fail BEFORE the fork so the child cannot possibly
        # have completed a refresh of its own when it reads the state.
        server.mode = "error"

        def child(send: Callable[[str], None]) -> None:
            send(f"state1={client.connection_state()}")
            reconnected = _await(lambda: client.connection_state() == "connected", within=12.0)
            send(f"state2={client.connection_state()} reconnected={reconnected}")

        pid, pipe = _fork_child(child)
        try:
            state1 = pipe.read_line(timeout=15.0)
            assert state1 != "state1=connected", (
                "child reported connected from inherited state without a fresh refresh"
            )
            assert state1.startswith("state1="), f"unexpected child message: {state1!r}"

            # Now let the child actually refresh; it must recover on its own.
            server.mode = "ok"
            server.generation = 2
            state2 = pipe.read_line(timeout=20.0)
            assert state2 == "state2=connected reconnected=True", (
                f"child never re-established a truthful connected state: {state2!r}"
            )
        finally:
            _reap(pid)
            pipe.close()
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# R1 / R2 — the at-fork hook is CHEAP: a child that never calls the SDK
# does no network, starts no threads, and touches nothing fork-hostile
# ----------------------------------------------------------------------


@requires_fork
@pytest.mark.no_macos_fork_isolation
def test_r1_child_that_never_calls_the_sdk_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rebuild is LAZY (Reforge's design). The at-fork handler only drops
    inherited state; nothing is rebuilt until the child actually uses the SDK.

    A forked child that never touches the client must therefore make ZERO
    requests and exit cleanly. Deliberately runs WITHOUT the macOS isolation
    fixture: an eager rebuild reaches ``requests`` -> ``proxy_bypass`` ->
    ``_scproxy`` -> the Objective-C runtime inside the fork handler, which
    libobjc kills the child for. With a lazy rebuild the child never gets
    anywhere near it, so no workaround is needed — that is the assertion.
    """
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    server = _ConfigServer()
    # 30s poll interval: the poller fetches once when it engages at init and
    # then goes quiet, so any SDK request during the child's lifetime is the
    # child's.
    client = _make_client(server, fallback_poll_interval_ms=30_000)
    try:
        client.init()
        _await_ready(client)
        before = _await_server_quiet(server)

        def child(send: Callable[[str], None]) -> None:
            # Never calls the SDK. Just proves the child got this far.
            send("alive")
            time.sleep(1.0)

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=10.0)
            status = _reap(pid, timeout=10.0)
        finally:
            pipe.close()
        time.sleep(0.5)
        after = server.sdk_requests

        assert message == "alive", f"child never ran: {message!r}"
        assert status == "exit=0", (
            f"a child that never calls the SDK must exit cleanly, got {status} "
            "(SIGSEGV/SIGTRAP here is the macOS _scproxy fork crash the eager "
            "rebuild walked into)"
        )
        assert after == before, (
            f"a child that never calls the SDK made {after - before} SDK request(s); "
            "the at-fork handler must not fetch"
        )
    finally:
        client.close()
        server.close()


@requires_fork
@pytest.mark.no_macos_fork_isolation
def test_r2_subprocess_with_preexec_fn_is_cheap_and_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPython runs ``after_in_child`` handlers in the pre-exec child of a
    ``subprocess`` spawn — but only when ``preexec_fn`` is given (that is what
    makes ``_posixsubprocess`` call ``PyOS_AfterFork_Child``). With an eager
    rebuild every such spawn paid a full SDK rebuild — fetch, threads, SSE — in
    a process that was about to ``exec`` anyway, and on macOS crashed outright.

    Two phases. 50 back-to-back spawns must all exit 0 (an eager rebuild
    crashes the pre-exec child on macOS, and races ``exec`` on Linux). Then a
    handful of spawns whose ``preexec_fn`` lingers briefly before ``exec`` —
    long enough that any fetch the handler fired would actually reach the
    server — must cause zero SDK requests.
    """
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    true_bin = shutil.which("true")
    assert true_bin is not None, "no /usr/bin/true on this platform"

    server = _ConfigServer()
    client = _make_client(server, fallback_poll_interval_ms=30_000)
    try:
        client.init()
        _await_ready(client)
        before = _await_server_quiet(server)

        codes = set()
        for _ in range(50):
            completed = subprocess.run([true_bin], preexec_fn=lambda: None)  # noqa: PLW1509
            codes.add(completed.returncode)

        # Linger in the pre-exec child so a fetch fired by the fork handler has
        # time to reach the server before `exec` wipes the process. Without
        # this, whether the eager rebuild's request lands is a race with exec.
        def _lingering_preexec() -> None:
            time.sleep(0.3)

        for _ in range(5):
            completed = subprocess.run([true_bin], preexec_fn=_lingering_preexec)  # noqa: PLW1509
            codes.add(completed.returncode)

        time.sleep(0.5)
        after = server.sdk_requests

        assert codes == {0}, f"preexec_fn spawns did not all exit 0: {sorted(codes)}"
        assert after == before, (
            f"preexec_fn spawns caused {after - before} SDK request(s); the "
            "at-fork handler must not fetch in a pre-exec child"
        )
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# R3 — the lazy rebuild runs exactly once, on first use, and the child
# serves its OWN freshly-fetched config (not the parent's snapshot)
# ----------------------------------------------------------------------


@requires_fork
def test_r3_child_rebuilds_once_on_first_use_and_serves_its_own_fetch() -> None:
    """The child comes out of the fork looking like a newly constructed client:
    empty store, generation 0, nothing initialized. Its first SDK call runs the
    normal ``init()`` path — blocking under the usual init-timeout semantics —
    so the very first value it serves is the CURRENT server config, not the
    snapshot the parent happened to hold at fork time. The rebuild happens
    exactly once no matter how many calls follow.
    """
    server = _ConfigServer()
    client = _make_client(server)
    try:
        client.init()
        _await_ready(client)
        assert client.held_generation() == 1

        # Count rebuilds from inside the child by wrapping the private hook on
        # the instance BEFORE the fork; the child inherits the wrapper.
        rebuilds: list[int] = []
        original_rebuild = client._rebuild_in_child

        def counting_rebuild() -> None:
            rebuilds.append(1)
            original_rebuild()

        client._rebuild_in_child = counting_rebuild  # type: ignore[method-assign]

        def child(send: Callable[[str], None]) -> None:
            # Give the parent time to publish AFTER the fork. A lazy child does
            # nothing at all in this window — which is what makes the first
            # fetch below land on the NEW generation.
            time.sleep(0.6)
            # Read PRIVATE state first: unlike the public accessors these do not
            # trigger the rebuild, so they observe the child exactly as the
            # at-fork handler left it. (Going through the public API here would
            # race the rebuild's own fetch, which can land before the accessor
            # returns. `connection_state()`'s post-fork truthfulness is covered
            # deterministically by T6.)
            before = (
                f"fresh_store={client._store.install_count() == 0 and client._store.get_generation() == 0} "
                f"no_liveness={client._last_successful_refresh is None} "
                f"uninitialized={not client._initialized.is_set()}"
            )
            # First evaluation: blocks on the child's own init + fetch.
            value = client.get_string("no.such.key", default="fallback")
            client.get_string("no.such.key", default="fallback")
            send(
                f"{before} value={value} gen={client.held_generation()} "
                f"installs={client.config_install_count()} rebuilds={len(rebuilds)} "
                f"state={client.connection_state()}"
            )

        pid, pipe = _fork_child(child)
        try:
            # Published AFTER the fork: only the child's own fetch can see it.
            server.generation = 2
            message = pipe.read_line(timeout=20.0)
        finally:
            _reap(pid)
            pipe.close()

        fields = dict(part.split("=", 1) for part in message.split(" "))
        assert fields.get("fresh_store") == "True", (
            f"child must start from an EMPTY store, not the parent's snapshot: {message!r}"
        )
        assert fields.get("no_liveness") == "True", (
            f"child inherited the parent's liveness stamp: {message!r}"
        )
        assert fields.get("uninitialized") == "True", (
            f"child must come out of the fork uninitialized, like a fresh client: {message!r}"
        )
        assert fields.get("value") == "fallback", message
        assert fields.get("gen") == "2", (
            f"child's first call must serve its own fetch of the CURRENT config: {message!r}"
        )
        assert fields.get("installs") == "1", (
            f"child installed {fields.get('installs')} envelopes, expected exactly 1: {message!r}"
        )
        assert fields.get("rebuilds") == "1", f"the lazy rebuild must run exactly once: {message!r}"
        assert fields.get("state") == "connected", message
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# The child's first fetch is a clean install — no phantom guard rejections
# ----------------------------------------------------------------------


@requires_fork
def test_child_first_fetch_is_not_counted_as_a_guard_rejection() -> None:
    """The reject-older guard drops an equal-or-older envelope and the drop is
    counted as ``guardRejected``, which feeds the ``sdk_failover`` alerting
    signal. A child that inherited the parent's store at the parent's
    generation would have its own first full 200 rejected at that same
    generation — a phantom rejection per forked worker, and ``resolved_from()``
    stuck at "" forever because it is only stamped on an accepted install.

    Because the child starts from an EMPTY store, its first fetch is accepted:
    zero guard rejections, and ``resolved_from()`` stamped by the child's own
    fetch — the same window the parent's own init produces.

    The fallback poller is off so the only fetch in play is the init fetch.
    With it on, init's fetch and the poller's engage-time fetch race at the
    same generation and one of them is guard-rejected — in the parent too.
    That is a real (pre-existing, non-fork) wart in how equal-generation
    re-delivery is counted, filed separately; it is not what this test is
    about.
    """
    server = _ConfigServer()
    client = _make_client(
        server,
        fallback_poll_enabled=False,
        collect_evaluation_summaries=True,
        context_upload_mode="periodic_example",
        telemetry_url="http://127.0.0.1:1",
    )
    try:
        client.init()
        _await_ready(client)
        assert client._telemetry is not None, "test needs telemetry enabled"

        # The parent's own init window is the baseline the child must match.
        parent_window = client._telemetry._failover_collector.drain()
        assert parent_window is not None
        assert parent_window.failover.guard_rejected == 0
        assert parent_window.failover.resolved_from_primary == 1

        def child(send: Callable[[str], None]) -> None:
            client.get_string("no.such.key", default="fallback")
            # Let any late install / rejection land before the window is read,
            # so a child whose fetch was still in flight cannot pass by racing.
            time.sleep(0.5)
            telemetry = client._telemetry
            assert telemetry is not None
            event = telemetry._failover_collector.drain()
            rejected = 0 if event is None else event.failover.guard_rejected
            primary = 0 if event is None else event.failover.resolved_from_primary
            send(
                f"guard_rejected={rejected} resolved_from_primary={primary} "
                f"resolved_from={client.resolved_from()!r}"
            )

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=20.0)
        finally:
            _reap(pid)
            pipe.close()

        assert message == "guard_rejected=0 resolved_from_primary=1 resolved_from='primary'", (
            f"child's first fetch was not a clean, stamped install: {message!r}"
        )
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# Registry hygiene
# ----------------------------------------------------------------------


def test_hook_is_registered_child_only() -> None:
    """Exactly one ``os.register_at_fork`` call exists anywhere in the package,
    and its only keyword is ``after_in_child`` — no ``before=``, no
    ``after_in_parent=``. The parent is never touched. This is the standing
    guard against re-introducing the parent-side teardown that caused the
    sdk-ruby incident (qfg-lv4n)."""
    import ast
    import pathlib

    import quonfig

    package_root = pathlib.Path(quonfig.__file__).parent
    calls: list[list[str]] = []
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register_at_fork"
            ):
                calls.append([kw.arg or "**kwargs" for kw in node.keywords])
                assert not node.args, f"{path}: register_at_fork must be called with keywords only"

    assert len(calls) == 1, f"expected exactly one register_at_fork call, found {calls}"
    assert calls[0] == ["after_in_child"], (
        f"the fork hook must be child-only; found keywords {calls[0]}"
    )


def test_registry_holds_weak_references() -> None:
    """A client that goes out of scope must not be kept alive by the registry."""
    import gc

    from quonfig._fork import _instances

    server = _ConfigServer()
    try:
        client: Optional[Quonfig] = _make_client(server)
        assert client in _instances
        before = len(_instances)
        client.close()
        client = None
        gc.collect()
        assert len(_instances) < before, "registry leaked a dead client"
    finally:
        server.close()


# ----------------------------------------------------------------------
# Second adversarial pass (qfg-lv4n.2). Each test below is one confirmed
# defect from the reviewer's probes in the lazy post-fork rebuild.
# ----------------------------------------------------------------------


def _fields(message: str) -> "dict[str, str]":
    """Parse a ``k=v k=v`` child message, failing loudly on ``<timeout>``."""
    assert "=" in message, f"unusable child message: {message!r}"
    return dict(part.split("=", 1) for part in message.split(" ") if "=" in part)


# ----------------------------------------------------------------------
# P1 — a grandchild forked from a child that has not used the SDK yet
# ----------------------------------------------------------------------


@requires_fork
def test_p1_grandchild_forked_before_first_use_still_rebuilds() -> None:
    """Gunicorn/Celery re-fork and ``multiprocessing`` pools fork again from a
    worker that has not touched the client yet. That child carries
    ``_started=False`` AND ``_needs_rebuild_after_fork=True``; the handler must
    treat it as pending, not as "never started", or the grandchild is
    permanently dead — every getter blocks the full ``init_timeout_ms`` and
    then raises (or returns defaults), and an explicit ``init()`` latches an
    empty, transport-less client.
    """
    server = _ConfigServer(configs=[_string_config("k", "v")])
    client = _make_client(server, fallback_poll_interval_ms=30_000, init_timeout_ms=3000)
    try:
        client.init()
        _await_ready(client)
        _await_server_quiet(server)
        # Published after the parent settled: only a fetch of the grandchild's
        # own can serve generation 2.
        server.generation = 2

        def child(send: Callable[[str], None]) -> None:
            # Deliberately does NOT use the SDK before forking again.
            def grandchild(send_g: Callable[[str], None]) -> None:
                started = time.monotonic()
                value = client.get_string("k", default="MISSING")
                send_g(
                    f"value={value} gen={client.held_generation()} "
                    f"elapsed={time.monotonic() - started:.2f}"
                )

            gpid, gpipe = _fork_child(grandchild)
            try:
                send(gpipe.read_line(timeout=25.0))
            finally:
                _reap(gpid, timeout=15.0)
                gpipe.close()

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=35.0)
        finally:
            _reap(pid, timeout=25.0)
            pipe.close()

        fields = _fields(message)
        assert fields.get("value") == "v", (
            f"grandchild forked before the child's first use never rebuilt: {message!r}"
        )
        assert fields.get("gen") == "2", (
            f"grandchild did not serve its own fetch of the current config: {message!r}"
        )
        assert float(fields["elapsed"]) < 2.0, (
            f"grandchild blocked on the init timeout instead of rebuilding: {message!r}"
        )
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# P2 — a client constructed but not init()ed before the fork
# ----------------------------------------------------------------------


@requires_fork
def test_p2_client_constructed_but_never_inited_can_be_started_in_the_child() -> None:
    """Construct in the master, ``init()`` in ``post_fork`` — the pattern the
    README's hook advice invites. The handler must not leave that client
    unstartable: nulling its components before the never-started early return
    sends the child's ``init()`` down the "no data source configured" path,
    which silently latches an empty store forever.
    """
    server = _ConfigServer(configs=[_string_config("k", "v")])
    # NOTE: no init() in the parent.
    client = _make_client(server, fallback_poll_interval_ms=30_000, init_timeout_ms=3000)
    try:

        def child(send: Callable[[str], None]) -> None:
            client.init()
            value = client.get_string("k", default="MISSING")
            send(
                f"value={value} gen={client.held_generation()} "
                f"threads={'|'.join(_quonfig_threads())}"
            )

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=25.0)
        finally:
            _reap(pid, timeout=20.0)
            pipe.close()

        fields = _fields(message)
        assert fields.get("value") == "v", (
            f"init() in the child latched an empty, transport-less client: {message!r}"
        )
        assert fields.get("gen") == "1", message
        assert "quonfig-fallback-poll" in fields.get("threads", ""), (
            f"the child's update channel was never started: {message!r}"
        )
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# P3 — close() in a child before its first use
# ----------------------------------------------------------------------


@requires_fork
def test_p3_close_before_first_use_answers_defaults_immediately() -> None:
    """``close()`` clears the rebuild flag, so nothing will ever set the fresh
    ``_initialized`` event the handler installed. Every later getter then
    blocks the full ``init_timeout_ms`` and raises (``on_init_failure="raise"``)
    — a closed client must answer instantly instead.
    """
    server = _ConfigServer(configs=[_string_config("k", "v")])
    client = _make_client(
        server,
        fallback_poll_interval_ms=30_000,
        init_timeout_ms=3000,
        on_init_failure="raise",
    )
    try:
        client.init()
        _await_ready(client)
        _await_server_quiet(server)

        def child(send: Callable[[str], None]) -> None:
            client.close()
            started = time.monotonic()
            try:
                value = client.get_string("k", default="fallback")
            except Exception as exc:  # noqa: BLE001 — reported, not raised
                value = f"EXC:{type(exc).__name__}"
            send(f"value={value} elapsed={time.monotonic() - started:.2f}")

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=20.0)
        finally:
            _reap(pid, timeout=15.0)
            pipe.close()

        fields = _fields(message)
        assert fields.get("value") == "fallback", (
            f"a getter on a client closed before its first use must return the "
            f"default, not raise: {message!r}"
        )
        assert float(fields["elapsed"]) < 1.0, (
            f"the getter blocked on the init timeout after close(): {message!r}"
        )
    finally:
        server.close()


# ----------------------------------------------------------------------
# P4 — close() racing the first-use rebuild
# ----------------------------------------------------------------------


@requires_fork
def test_p4_close_racing_the_first_use_rebuild_leaves_no_live_threads() -> None:
    """``close()`` did not take the rebuild lock, so a reporter built by a
    rebuild already in flight was started AFTER the teardown had walked past it
    — a live ``quonfig-telemetry`` thread on a closed client, POSTing the
    child's telemetry for the rest of the process's life.

    The 300ms window is injected by wrapping the rebuild; that is the race the
    reviewer's ``probe_close_race.py`` (b) reproduces.
    """
    server = _ConfigServer(configs=[_string_config("k", "v")])
    client = _make_client(
        server,
        collect_evaluation_summaries=True,
        context_upload_mode="periodic_example",
        telemetry_url="http://127.0.0.1:1",
    )
    try:
        client.init()
        _await_ready(client)
        _await_server_quiet(server)

        def child(send: Callable[[str], None]) -> None:
            original_rebuild = client._rebuild_in_child
            rebuild_started = threading.Event()

            def slow_rebuild() -> None:
                rebuild_started.set()
                time.sleep(0.3)  # close() lands in this window
                original_rebuild()

            client._rebuild_in_child = slow_rebuild  # type: ignore[method-assign]
            result: dict = {}

            def first_use() -> None:
                try:
                    result["value"] = client.get_string("k", default="MISSING")
                except Exception as exc:  # noqa: BLE001
                    result["value"] = f"EXC:{type(exc).__name__}"

            user = threading.Thread(target=first_use, name="first-use")
            user.start()
            rebuild_started.wait(timeout=10.0)
            time.sleep(0.05)
            client.close()
            user.join(timeout=20.0)
            time.sleep(0.8)  # let a leaked thread show itself
            send(
                f"value={result.get('value')} shutdown={client._shutdown.is_set()} "
                f"threads={'|'.join(_quonfig_threads())}"
            )

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=30.0)
        finally:
            _reap(pid, timeout=20.0)
            pipe.close()

        fields = _fields(message)
        assert fields.get("shutdown") == "True", message
        assert "quonfig-telemetry" not in fields.get("threads", ""), (
            f"close() left a live telemetry thread on a closed client: {message!r}"
        )
        assert fields.get("threads") == "", (
            f"close() left SDK threads running in the child: {message!r}"
        )
    finally:
        client.close()
        server.close()


# ----------------------------------------------------------------------
# P6 — watchfiles imports `platform` on its watcher thread
# ----------------------------------------------------------------------


def test_p6_datadir_watcher_imports_platform_eagerly() -> None:
    """``watchfiles/main.py`` does ``import platform`` INSIDE ``watch()``, i.e.
    on the watcher thread. Fork while that import is in flight and the child
    inherits a half-initialized ``platform`` module; its watcher dies with
    ``AttributeError: partially initialized module 'platform'`` and the child
    silently has no datadir watcher at all.

    Importing ``platform`` at the top of our own module makes the PARENT
    complete it on the main thread, at import time, before any fork can
    interleave — after which ``watchfiles``'s function-level import is a
    ``sys.modules`` hit and cannot be half-done.

    The race itself is not deterministically reproducible in a test; this
    asserts the property that closes it.
    """
    from quonfig import datadir_watcher

    assert datadir_watcher.platform.system(), (
        "quonfig.datadir_watcher must import `platform` at module import time"
    )


# ----------------------------------------------------------------------
# P7 — diagnostics never trigger the rebuild (cross-SDK ruling, epic qfg-lv4n)
# ----------------------------------------------------------------------


@requires_fork
def test_p7_diagnostics_do_not_trigger_the_rebuild() -> None:
    """Health accessors are diagnostic-only. A liveness probe, a metrics
    scrape or a log line must not start threads and fire a config fetch from a
    forked child that has not evaluated anything yet. Until its first
    evaluation the child answers the pre-start state: ``initializing``, not
    ready, generation 0, no liveness stamp. Matches sdk-ruby.
    """
    server = _ConfigServer(configs=[_string_config("k", "v")])
    client = _make_client(server, fallback_poll_interval_ms=30_000)
    try:
        client.init()
        _await_ready(client)
        baseline = _await_server_quiet(server)

        def child(send: Callable[[str], None]) -> None:
            state = client.connection_state()
            is_ready = client.ready()
            generation = client.held_generation()
            no_stamp = client.last_successful_refresh() is None
            time.sleep(0.7)  # a rebuild that should not have happened would show here
            send(
                f"state={state} ready={is_ready} gen={generation} no_stamp={no_stamp} "
                f"threads={'|'.join(_quonfig_threads())}"
            )

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=20.0)
        finally:
            _reap(pid, timeout=15.0)
            pipe.close()
        time.sleep(0.5)  # let any in-flight child request land on the server

        fields = _fields(message)
        assert fields.get("state") == "initializing", (
            f"a not-yet-rebuilt child must report the pre-start state: {message!r}"
        )
        assert fields.get("ready") == "False", message
        assert fields.get("gen") == "0", message
        assert fields.get("no_stamp") == "True", message
        assert fields.get("threads") == "", (
            f"diagnostics started SDK threads in the child: {message!r}"
        )
        assert server.sdk_requests == baseline, (
            f"diagnostics fired {server.sdk_requests - baseline} config fetch(es) "
            f"from a child that never evaluated anything"
        )
    finally:
        client.close()
        server.close()


@requires_fork
def test_p7_keys_in_a_pending_child_returns_its_own_fetched_content() -> None:
    """``keys()`` and ``raw_config()`` are content readers, not diagnostics:
    they DO trigger the rebuild, and they wait for the child's own fetch, so
    they never hand back the empty pre-fetch store."""
    server = _ConfigServer(configs=[_string_config("k", "v")])
    client = _make_client(server, fallback_poll_interval_ms=30_000)
    try:
        client.init()
        _await_ready(client)
        _await_server_quiet(server)

        def child(send: Callable[[str], None]) -> None:
            keys = client.keys()
            has_raw = client.raw_config("k") is not None
            send(f"keys={'|'.join(keys)} raw={has_raw}")

        pid, pipe = _fork_child(child)
        try:
            message = pipe.read_line(timeout=20.0)
        finally:
            _reap(pid, timeout=15.0)
            pipe.close()

        fields = _fields(message)
        assert fields.get("keys") == "k", (
            f"keys() returned the empty pre-fetch store in the child: {message!r}"
        )
        assert fields.get("raw") == "True", message
    finally:
        client.close()
        server.close()
