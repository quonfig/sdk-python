"""Tests for opt-in data_dir_auto_reload (qfg-mol-3gy).

Mirrors sdk-node's test/datadir-auto-reload.test.ts. There are two layers here:

* End-to-end tests that stand up a temp datadir, mutate it on disk, and wait
  for a real `watchfiles` notification to drive the reload. These prove the
  SDK is wired to a working filesystem notifier. They are skipped on macOS —
  see `_NO_RELIABLE_MACOS_WATCH` for the measurements behind that.
* Deterministic tests that drive `DatadirWatcher`'s consumer loop through a
  stub `watchfiles.watch`. These cover everything the SDK itself owns —
  symlink resolution, the options handed to watchfiles, parse-then-swap,
  callback firing, shutdown — with no dependence on OS notification timing,
  so they run everywhere, macOS included.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest

from quonfig import Quonfig
from quonfig import datadir_watcher as datadir_watcher_module

# Wall-clock budget for "the watcher noticed a change on disk". Generous on
# purpose — what is under test is the reload wiring, not how fast a given
# platform's notification backend gets around to telling us.
_WATCH_TIMEOUT = 10.0

# Settle window for the "nothing should have happened" assertions. Long enough
# to cover a detection cycle plus the debounce, so those assertions are not
# passing simply by finishing before the watcher could have fired.
_SETTLE = 1.5

# Value used to prove the watch is attached before a scenario starts.
_LIVENESS_VALUE = "__watcher-liveness-probe__"

# Neither watchfiles backend delivers reliably on macOS, so the tests that
# depend on a real notification run on Linux CI only (qfg-x66x). Measured on
# Darwin 25.2 / watchfiles 1.2.0, which is the newest release:
#
#   * FSEvents (the default): a single write is reported after 2.6s, 6.2s, or
#     not within 15s, run to run — and never at all from inside a sandboxed
#     process. No test budget makes that deterministic.
#   * WATCHFILES_FORCE_POLLING: a write landing shortly after a previously
#     reported change is dropped permanently, not merely late — still absent
#     20s later, 7 times out of 9 at a 0.5s spacing.
#
# Both are properties of watchfiles/notify on macOS, not of DatadirWatcher:
# the stub-driven tests below exercise every line of SDK code these do.
_NO_RELIABLE_MACOS_WATCH = pytest.mark.skipif(
    sys.platform == "darwin",
    reason="watchfiles delivers unreliably on macOS (FSEvents latency / polling drops); "
    "the stub-driven tests in this file cover the same SDK code (qfg-x66x)",
)


# --- helpers ---------------------------------------------------------------


def _write_workspace(base: Path, value: str, *, environment: str = "production") -> None:
    """Build a minimal datadir with one valid string config keyed `welcome-message`."""
    (base / "configs").mkdir(parents=True, exist_ok=True)
    _write_greeting(base, value)
    (base / "quonfig.json").write_text(json.dumps({"environments": [environment]}))


def _write_greeting(base: Path, value: str) -> None:
    (base / "configs" / "welcome-message.json").write_text(
        json.dumps(
            {
                "id": "welcome-message",
                "key": "welcome-message",
                "type": "config",
                "valueType": "string",
                "sendToClientSdk": False,
                "default": {
                    "rules": [
                        {
                            "criteria": [{"operator": "ALWAYS_TRUE"}],
                            "value": {"type": "string", "value": value},
                        }
                    ]
                },
            }
        )
    )


def _poll_until(predicate, timeout: float, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _wait_for(predicate, timeout: float = _WATCH_TIMEOUT, interval: float = 0.02) -> None:
    if not _poll_until(predicate, timeout, interval):
        raise AssertionError(f"Timed out after {timeout}s waiting for predicate")


def _await_watcher_live(
    client: Quonfig,
    base: Path,
    timeout: float = _WATCH_TIMEOUT,
    restore: str | None = None,
) -> None:
    """Block until the watcher demonstrably delivers changes made under `base`.

    `init()` starts the watcher thread and returns immediately; the underlying
    watch is registered later, on that thread. A test that writes into the
    gap loses the event permanently and then fails on a timeout that has
    nothing to do with the code under test — the startup race behind the
    intermittent failures in qfg-x66x. Re-writing a sentinel until it is
    observed proves the watch is attached before the scenario begins, which is
    what makes the assertions that follow deterministic instead of lucky.

    Pass `restore` to put a known value back afterwards (and wait for it), for
    tests that assert on the config value rather than overwriting it.
    """
    deadline = time.monotonic() + timeout
    attempt = 0
    live = False
    while not live and time.monotonic() < deadline:
        attempt += 1
        probe = f"{_LIVENESS_VALUE}-{attempt}"
        _write_greeting(base, probe)
        live = _poll_until(
            lambda expected=probe: client.get_string("welcome-message") == expected, 0.75
        )
    if not live:
        raise AssertionError(
            f"datadir watcher never delivered a change within {timeout}s; "
            "cannot distinguish a watcher bug from a lost startup event"
        )
    if restore is not None:
        _write_greeting(base, restore)
        _wait_for(lambda: client.get_string("welcome-message") == restore)


class _StubWatch:
    """Stand-in for `watchfiles.watch` that yields batches when told to.

    `DatadirWatcher` resolves `_watch` from its module namespace at call time,
    so swapping this in gives the watcher thread a notification source the
    test drives directly. Everything below the yield is untouched SDK code —
    the thread, the stop event, `_on_change`, the client's parse-then-swap —
    and nothing depends on how quickly (or whether) the OS reports a write.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.watching = threading.Event()
        self.handled = 0
        self._batches: queue.Queue[set] = queue.Queue()

    def __call__(self, path: str, **kwargs: object) -> Iterator[set]:
        self.calls.append((path, kwargs))
        stop_event = kwargs["stop_event"]
        assert isinstance(stop_event, threading.Event)
        self.watching.set()
        while not stop_event.is_set():
            try:
                batch = self._batches.get(timeout=0.01)
            except queue.Empty:
                continue
            yield batch
            # Control comes back here only once DatadirWatcher has returned
            # from `on_change` for that batch, which is what lets `emit()`
            # be synchronous and the tests be ordered rather than raced.
            self.handled += 1

    def emit(self, path: Path, *, wait: bool = True) -> None:
        """Report `path` as changed, exactly as a watchfiles batch would.

        Blocks until the SDK has finished handling the batch unless
        `wait=False`, which is for asserting that a batch is *not* handled.
        """
        target = self.handled + 1
        self._batches.put({("modified", str(path))})
        if wait and not _poll_until(lambda: self.handled >= target, _WATCH_TIMEOUT):
            raise AssertionError("the watcher never handled the emitted change")

    @property
    def watched_path(self) -> str:
        assert self.calls, "watchfiles.watch was never called"
        return self.calls[0][0]

    @property
    def watch_kwargs(self) -> dict:
        assert self.calls, "watchfiles.watch was never called"
        return self.calls[0][1]


@pytest.fixture
def stub_watch(monkeypatch: pytest.MonkeyPatch) -> _StubWatch:
    stub = _StubWatch()
    monkeypatch.setattr(datadir_watcher_module, "_watch", stub)
    return stub


# --- tests ----------------------------------------------------------------


@_NO_RELIABLE_MACOS_WATCH
def test_reloads_envelope_and_fires_callback_on_file_change(tmp_path: Path) -> None:
    _write_workspace(tmp_path, "hola")

    calls: list[None] = []
    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=30,
        on_config_update=lambda: calls.append(None),
    )
    try:
        client.init()
        assert client.get_string("welcome-message") == "hola"

        _await_watcher_live(client, tmp_path)
        initial_calls = len(calls)

        _write_greeting(tmp_path, "buenos-dias")
        _wait_for(lambda: client.get_string("welcome-message") == "buenos-dias")
        assert len(calls) > initial_calls
    finally:
        client.close()


def test_disabled_by_default_does_not_reload(tmp_path: Path) -> None:
    _write_workspace(tmp_path, "hola")

    calls: list[None] = []
    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        on_config_update=lambda: calls.append(None),
    )
    try:
        client.init()
        baseline = len(calls)
        assert client.get_string("welcome-message") == "hola"

        _write_greeting(tmp_path, "ignored")
        time.sleep(_SETTLE)  # generous debounce window

        assert len(calls) == baseline
        assert client.get_string("welcome-message") == "hola"
    finally:
        client.close()


@_NO_RELIABLE_MACOS_WATCH
def test_debounces_burst_of_writes_into_a_single_reload(tmp_path: Path) -> None:
    _write_workspace(tmp_path, "v0")

    extra_calls: list[None] = []
    initial_done = threading.Event()

    def cb() -> None:
        if initial_done.is_set():
            extra_calls.append(None)

    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=200,
        on_config_update=cb,
    )
    try:
        client.init()
        _await_watcher_live(client, tmp_path)
        initial_done.set()

        # Write the burst as fast as the filesystem takes it. The earlier
        # version slept 10ms between writes, spreading the burst over ~50ms —
        # which is exactly watchfiles' `step`, the quiet period that decides
        # where one yielded batch ends and the next begins. Any scheduling
        # hiccup inside that loop split the burst in two, which is how this
        # test flaked on a loaded ubuntu runner (2 callbacks, not 1).
        writes = 5
        for i in range(1, writes + 1):
            _write_greeting(tmp_path, f"v{i}")

        _wait_for(lambda: client.get_string("welcome-message") == f"v{writes}")
        # Let any straggler debounce timer fire.
        time.sleep(_SETTLE)

        # Assert coalescing, not an exact count. The batch boundary belongs to
        # watchfiles — it yields after a quiet `step`, not after our debounce —
        # so "exactly 1" is a bet on the OS scheduler. "Fewer reloads than
        # writes, and the last write won" is the property we actually care
        # about, and it cannot race a timer.
        assert 1 <= len(extra_calls) < writes, (
            f"expected a burst of {writes} writes to coalesce into fewer than {writes} "
            f"reload callbacks, got {len(extra_calls)}"
        )
        assert client.get_string("welcome-message") == f"v{writes}"
    finally:
        client.close()


@_NO_RELIABLE_MACOS_WATCH
def test_parse_then_swap_keeps_previous_envelope_on_malformed_json(tmp_path: Path) -> None:
    _write_workspace(tmp_path, "hola")

    extra_calls: list[None] = []
    initial_done = threading.Event()

    def cb() -> None:
        if initial_done.is_set():
            extra_calls.append(None)

    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=30,
        on_config_update=cb,
    )
    try:
        client.init()
        _await_watcher_live(client, tmp_path, restore="hola")
        initial_done.set()

        # Garbage: load_datadir's per-file try/except will reject the file and,
        # with no other configs in the dir, raise RuntimeError("No configs
        # loaded ..."). The reload path must swallow that and keep the prior
        # envelope rather than blanking the store.
        (tmp_path / "configs" / "welcome-message.json").write_text("{not valid json")
        time.sleep(_SETTLE)

        assert client.get_string("welcome-message") == "hola"
        assert len(extra_calls) == 0

        # Prove the settle above was not vacuous: the watcher is still live and
        # still reloading, so the malformed write really was seen and rejected
        # rather than simply not noticed yet.
        _write_greeting(tmp_path, "recovered")
        _wait_for(lambda: client.get_string("welcome-message") == "recovered")
    finally:
        client.close()


def test_close_stops_the_watcher_thread(tmp_path: Path) -> None:
    _write_workspace(tmp_path, "hola")

    extra_calls: list[None] = []
    initial_done = threading.Event()

    def cb() -> None:
        if initial_done.is_set():
            extra_calls.append(None)

    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=30,
        on_config_update=cb,
    )
    client.init()
    initial_done.set()

    # Capture watcher thread before close so we can verify it exits.
    watcher_threads_before = [
        t for t in threading.enumerate() if t.name.startswith("quonfig-datadir-watcher")
    ]
    assert watcher_threads_before, "expected a quonfig-datadir-watcher thread while running"

    client.close()

    # Watcher thread should exit within a reasonable shutdown window.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        alive = [t for t in watcher_threads_before if t.is_alive()]
        if not alive:
            break
        time.sleep(0.05)
    still_alive = [t for t in watcher_threads_before if t.is_alive()]
    assert not still_alive, f"watcher threads did not exit after close(): {still_alive}"

    # And further file edits should not produce any more callbacks.
    _write_greeting(tmp_path, "after-close")
    time.sleep(_SETTLE)
    assert len(extra_calls) == 0


def test_watcher_registration_failure_downgrades_gracefully(tmp_path: Path) -> None:
    """Pointing at a non-existent path must not crash init — the loader will
    raise, but if we somehow bypass that (e.g. dir disappears after load),
    a watcher registration failure should only log and continue."""

    # Use a directory that exists at init time but disappears before the
    # watcher attaches: simulate via a path Quonfig hasn't loaded from. We
    # invoke the watcher directly to exercise the error path without racing.
    from quonfig.datadir_watcher import DatadirWatcher

    errors: list[BaseException] = []

    def on_change() -> None:
        raise AssertionError("on_change should not fire when registration fails")

    def on_error(err: BaseException) -> None:
        errors.append(err)

    missing = tmp_path / "does-not-exist"
    watcher = DatadirWatcher(
        datadir=str(missing),
        debounce_ms=10,
        on_change=on_change,
        on_error=on_error,
    )
    assert watcher.start() is False
    assert errors, "expected on_error to be invoked on registration failure"
    watcher.close()


@_NO_RELIABLE_MACOS_WATCH
def test_follows_symlinked_datadir(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    _write_workspace(real_dir, "hola")

    link_path = tmp_path / "datadir-link"
    os.symlink(real_dir, link_path, target_is_directory=True)

    extra_calls: list[None] = []
    initial_done = threading.Event()

    def cb() -> None:
        if initial_done.is_set():
            extra_calls.append(None)

    client = Quonfig(
        datadir=str(link_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=30,
        on_config_update=cb,
    )
    try:
        client.init()
        # Writes go to the real directory, not the link, so this also proves
        # `DatadirWatcher.start()` resolved the symlink before watching.
        _await_watcher_live(client, real_dir)
        initial_done.set()

        _write_greeting(real_dir, "via-symlink")
        _wait_for(lambda: client.get_string("welcome-message") == "via-symlink")
        assert len(extra_calls) > 0
    finally:
        client.close()


# --- stub-driven tests (no dependence on OS notification timing) -----------


def test_watch_event_reloads_envelope_and_fires_callback(
    tmp_path: Path, stub_watch: _StubWatch
) -> None:
    _write_workspace(tmp_path, "hola")

    calls: list[None] = []
    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=30,
        on_config_update=lambda: calls.append(None),
    )
    try:
        client.init()
        assert stub_watch.watching.wait(_WATCH_TIMEOUT), "watcher never started watching"
        assert client.get_string("welcome-message") == "hola"
        initial_calls = len(calls)

        _write_greeting(tmp_path, "buenos-dias")
        stub_watch.emit(tmp_path / "configs" / "welcome-message.json")

        _wait_for(lambda: client.get_string("welcome-message") == "buenos-dias")
        assert len(calls) > initial_calls
    finally:
        client.close()


def test_watch_options_resolve_symlink_and_carry_the_configured_debounce(
    tmp_path: Path, stub_watch: _StubWatch
) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    _write_workspace(real_dir, "hola")

    link_path = tmp_path / "datadir-link"
    os.symlink(real_dir, link_path, target_is_directory=True)

    client = Quonfig(
        datadir=str(link_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=123,
    )
    try:
        client.init()
        assert stub_watch.watching.wait(_WATCH_TIMEOUT), "watcher never started watching"

        # The symlink is resolved before watching, so edits to the real
        # directory are seen even on backends that do not follow links.
        assert stub_watch.watched_path == os.path.realpath(link_path)
        assert stub_watch.watch_kwargs["debounce"] == 123
        assert stub_watch.watch_kwargs["recursive"] is True
    finally:
        client.close()


def test_watch_event_on_malformed_json_keeps_previous_envelope(
    tmp_path: Path, stub_watch: _StubWatch
) -> None:
    _write_workspace(tmp_path, "hola")

    extra_calls: list[None] = []
    initial_done = threading.Event()

    def cb() -> None:
        if initial_done.is_set():
            extra_calls.append(None)

    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=30,
        on_config_update=cb,
    )
    config_file = tmp_path / "configs" / "welcome-message.json"
    try:
        client.init()
        assert stub_watch.watching.wait(_WATCH_TIMEOUT), "watcher never started watching"
        initial_done.set()

        # Garbage: load_datadir's per-file try/except rejects the file and,
        # with no other configs in the dir, raises RuntimeError("No configs
        # loaded ..."). The reload path must swallow that and keep the prior
        # envelope rather than blanking the store.
        config_file.write_text("{not valid json")
        stub_watch.emit(config_file)  # returns once the failed reload is done

        assert client.get_string("welcome-message") == "hola"
        assert extra_calls == []

        # And the watcher survives the failure: the next good write reloads.
        _write_greeting(tmp_path, "recovered")
        stub_watch.emit(config_file)

        assert client.get_string("welcome-message") == "recovered"
        assert len(extra_calls) == 1, (
            f"only the successful reload should fire on_config_update, got {len(extra_calls)}"
        )
    finally:
        client.close()


def test_close_stops_forwarding_watch_events(tmp_path: Path, stub_watch: _StubWatch) -> None:
    _write_workspace(tmp_path, "hola")

    extra_calls: list[None] = []
    initial_done = threading.Event()

    def cb() -> None:
        if initial_done.is_set():
            extra_calls.append(None)

    client = Quonfig(
        datadir=str(tmp_path),
        environment="production",
        data_dir_auto_reload=True,
        data_dir_auto_reload_debounce_ms=30,
        on_config_update=cb,
    )
    client.init()
    assert stub_watch.watching.wait(_WATCH_TIMEOUT), "watcher never started watching"
    initial_done.set()

    # Prove the wiring delivers before asserting that close() stops it,
    # otherwise this passes for a watcher that never worked at all.
    _write_greeting(tmp_path, "before-close")
    stub_watch.emit(tmp_path / "configs" / "welcome-message.json")
    _wait_for(lambda: client.get_string("welcome-message") == "before-close")
    assert len(extra_calls) == 1

    client.close()

    _write_greeting(tmp_path, "after-close")
    stub_watch.emit(tmp_path / "configs" / "welcome-message.json", wait=False)
    time.sleep(0.2)
    assert client.get_string("welcome-message") == "before-close"
    assert len(extra_calls) == 1


@pytest.fixture(autouse=True)
def _no_stray_init_threads():
    """Catch test leaks early — fail fast if a watcher thread survives the test."""
    before = {t.name for t in threading.enumerate()}
    yield
    # Give backgrounded shutdowns a brief grace window.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        after = {t.name for t in threading.enumerate()} - before
        leaked = [n for n in after if n.startswith("quonfig-datadir-watcher")]
        if not leaked:
            return
        time.sleep(0.05)
    after = {t.name for t in threading.enumerate()} - before
    leaked = [n for n in after if n.startswith("quonfig-datadir-watcher")]
    assert not leaked, f"watcher threads leaked across test: {leaked}"
