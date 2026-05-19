"""Tests for opt-in data_dir_auto_reload (qfg-mol-3gy).

Mirrors sdk-node's test/datadir-auto-reload.test.ts. Each test stands up a
temp datadir with a single string Config, opens a Quonfig client in datadir
mode with auto-reload on, mutates the file on disk, and asserts the envelope
re-loads (or stays put, depending on the scenario).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from quonfig import Quonfig

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


def _wait_for(predicate, timeout: float = 3.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"Timed out after {timeout}s waiting for predicate")


# --- tests ----------------------------------------------------------------


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
        initial_calls = len(calls)

        _write_greeting(tmp_path, "buenos-dias")
        _wait_for(lambda: client.get_string("welcome-message") == "buenos-dias", timeout=5.0)
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
        time.sleep(0.3)  # generous debounce window

        assert len(calls) == baseline
        assert client.get_string("welcome-message") == "hola"
    finally:
        client.close()


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
        initial_done.set()

        for i in range(1, 6):
            _write_greeting(tmp_path, f"v{i}")
            time.sleep(0.01)

        _wait_for(lambda: client.get_string("welcome-message") == "v5", timeout=5.0)
        # Let any straggler debounce timer fire.
        time.sleep(0.5)

        assert len(extra_calls) == 1, (
            f"Expected exactly 1 coalesced reload callback, got {len(extra_calls)}"
        )
    finally:
        client.close()


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
        initial_done.set()

        # Garbage: load_datadir's per-file try/except will reject the file and,
        # with no other configs in the dir, raise RuntimeError("No configs
        # loaded ..."). The reload path must swallow that and keep the prior
        # envelope rather than blanking the store.
        (tmp_path / "configs" / "welcome-message.json").write_text("{not valid json")
        time.sleep(0.4)

        assert client.get_string("welcome-message") == "hola"
        assert len(extra_calls) == 0
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
    time.sleep(0.3)
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
        initial_done.set()

        _write_greeting(real_dir, "via-symlink")
        _wait_for(lambda: client.get_string("welcome-message") == "via-symlink", timeout=5.0)
        assert len(extra_calls) > 0
    finally:
        client.close()


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
