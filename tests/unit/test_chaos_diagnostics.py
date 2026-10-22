"""Test-only chaos diagnostics hook (qfg-6vno.5).

``Quonfig._chaos_diagnostics()`` is the SDK-side source for the counters the
cross-SDK chaos scenarios assert on (``quonfig_sse_connect_attempts_total``,
``quonfig_sdk_worker_restart_total{layer=1}``, ``processStillAlive()``), so the
chaos probe never synthesizes them. Not public API.
"""

from __future__ import annotations

import threading
import time
from typing import List

from quonfig import Quonfig
from quonfig.sse import SSEClient
from quonfig.store import ConfigStore

DEAD = "http://127.0.0.1:1"


class _StubTransport:
    def _headers(self, extra=None):  # noqa: ANN001, ANN202
        return dict(extra or {})

    def _current_stream_url(self) -> str:
        return DEAD


def _wait_for(cond, timeout: float = 5.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_sse_worker_reports_each_attempt_and_each_restart() -> None:
    events: List[str] = []
    lock = threading.Lock()

    def record(kind: str) -> None:
        with lock:
            events.append(kind)

    stop = threading.Event()
    sse = SSEClient(
        _StubTransport(),  # type: ignore[arg-type]
        ConfigStore(),
        stop,
        wait_hook=lambda kind, s: 0.01,
        on_diagnostic=record,
    )
    sse.start()
    try:
        assert _wait_for(lambda: events.count("worker_restart") >= 3)
    finally:
        sse.stop()
        sse.join(2.0)
    with lock:
        attempts = events.count("sse_connect_attempt")
        restarts = events.count("worker_restart")
    # Every restart follows an attempt that ended abnormally (refused).
    assert attempts >= restarts >= 3


def _client(**kw) -> Quonfig:  # noqa: ANN003
    return Quonfig(
        sdk_key="test-key",
        api_urls=[DEAD],
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        **kw,
    )


def test_callback_throw_caught_by_supervisor_counts_a_layer1_restart() -> None:
    def boom() -> None:
        raise RuntimeError("user callback")

    c = _client(on_config_update=boom)
    try:
        assert c._chaos_diagnostics()["worker_restart_layer1"] == 0
        c._fire_on_config_update()
        c._fire_on_config_update()
        assert c._chaos_diagnostics()["worker_restart_layer1"] == 2
    finally:
        c.close()


def test_client_diagnostics_track_the_live_sse_worker() -> None:
    c = _client(init_timeout_ms=200, on_init_failure="continue", fallback_poll_enabled=False)
    c._test_wait_hook = lambda kind, s: 0.01 if kind == "sse" else s
    try:
        before = c._chaos_diagnostics()
        assert before["sse_connect_attempts"] == 0
        assert before["sse_worker_alive"] is False
        c.init()
        assert _wait_for(lambda: c._chaos_diagnostics()["worker_restart_layer1"] >= 2)
        d = c._chaos_diagnostics()
        assert d["sse_connect_attempts"] >= d["worker_restart_layer1"] >= 2
        assert d["sse_worker_alive"] is True
    finally:
        c.close()
    assert _wait_for(lambda: c._chaos_diagnostics()["sse_worker_alive"] is False)
