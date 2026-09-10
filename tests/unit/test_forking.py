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
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

import pytest

from quonfig import Quonfig

requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")


# ----------------------------------------------------------------------
# In-process config server (lives in the parent; the child reaches it over
# the socket, so a post-fork publish is only visible to a live child poller)
# ----------------------------------------------------------------------


class _ConfigServer:
    """Serves ``GET /api/v2/configs``. ``generation`` and ``mode`` are mutated
    by the parent at will; ``mode="error"`` answers 503 so no fetch can
    succeed (and therefore no liveness stamp can advance)."""

    def __init__(self) -> None:
        self.generation = 1
        self.mode = "ok"
        outer = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:  # noqa: N802
                if outer.mode == "error":
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(
                    {
                        "configs": [],
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


def _reap(pid: int, timeout: float = 10.0) -> None:
    """Wait for the child, SIGKILLing it if it wedged (a deadlocked child must
    not hang the suite)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return
        time.sleep(0.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)


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
# Registry hygiene
# ----------------------------------------------------------------------


@requires_fork
def test_hook_is_registered_child_only() -> None:
    """No before-fork or after-in-parent handler exists anywhere in the SDK —
    the parent is never touched. Guards against a re-introduction of the
    parent-side teardown that caused the sdk-ruby incident."""
    import quonfig._fork as fork_module

    source = os.path.join(os.path.dirname(fork_module.__file__), "_fork.py")
    with open(source, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "after_in_child=" in text
    assert "before=" not in text, "the fork hook must not register a before-fork handler"
    assert "after_in_parent=" not in text, (
        "the fork hook must not register an after-in-parent handler"
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
