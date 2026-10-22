"""Callback-throw isolation (supervisor contract Test 5; qfg-6vno.29).

A raise from a user callback (``on_config_update``,
``on_sse_connection_state_change``) is caught by the SDK, counted in the
test-only ``callback_errors`` diagnostics
(``quonfig_sdk_callback_errors_total{callback}``), logged at ERROR with the
exception and its traceback, and is NOT a worker restart. The SDK keeps
installing envelopes and the callback runs again on its next trigger.

Driven against a local SSE server (same fixture as Test 9): the init fetch
installs v1, then the stream emits v2 and v3 back-to-back and holds.
"""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict, Iterator, List

import pytest

from quonfig import Quonfig
from tests.unit.test_supervisor import (
    KEY,
    T9_KEY,
    Rig,
    sse_open,
    sse_write,
    t9_envelope,
    wait_for,
)


class _Run:
    def __init__(self) -> None:
        self.rig = Rig(sse_status=200)
        self.hold = threading.Event()
        self.gate = threading.Event()
        self.client: Any = None

    def _sse(self, h: BaseHTTPRequestHandler) -> None:
        self.gate.wait(5)
        sse_open(h)
        sse_write(h, b"data: " + t9_envelope(2, "v2") + b"\n\n")
        sse_write(h, b"data: " + t9_envelope(3, "v3") + b"\n\n")
        self.hold.wait(10)

    def _config(self, h: BaseHTTPRequestHandler) -> None:
        Rig._reply(h, 200, t9_envelope(1, "v1"))

    def start(self, **callbacks: Any) -> Quonfig:
        sse_url = self.rig._serve(self._sse)
        cfg_url = self.rig._serve(self._config)
        c = Quonfig(
            sdk_key=KEY,
            api_urls=[cfg_url],
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            init_timeout_ms=8000,
            fallback_poll_interval_ms=60000,
            **callbacks,
        )
        assert c._transport is not None
        c._transport._Transport__test_stream_url_override = sse_url  # type: ignore[attr-defined]
        self.client = c
        c.init()
        assert c._initialized.wait(8)
        self.gate.set()
        return c

    def stop(self) -> None:
        self.hold.set()
        self.gate.set()
        if self.client is not None:
            self.client.close()
        for s in self.rig.servers:
            s.shutdown()
            s.server_close()


@pytest.fixture
def run() -> Iterator[_Run]:
    r = _Run()
    yield r
    r.stop()


@pytest.fixture
def records() -> Iterator[List[logging.LogRecord]]:
    out: List[logging.LogRecord] = []

    class H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            out.append(record)

    h = H(level=logging.DEBUG)
    qlog = logging.getLogger("quonfig")
    old = qlog.level
    qlog.setLevel(logging.DEBUG)
    qlog.addHandler(h)
    yield out
    qlog.removeHandler(h)
    qlog.setLevel(old)


def _throw_once(message: str, calls: List[Any]) -> Callable[..., None]:
    def cb(*args: Any) -> None:
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError(message)

    return cb


def _error_logs_with_trace(records: List[logging.LogRecord], message: str) -> List[str]:
    out = []
    for r in list(records):
        if r.levelno != logging.ERROR or r.exc_info is None:
            continue
        text = logging.Formatter().format(r)
        if message in text and "Traceback" in text:
            out.append(text)
    return out


def test_on_config_update_throw_is_a_callback_error_not_a_restart(
    run: _Run, records: List[logging.LogRecord]
) -> None:
    calls: List[Any] = []
    c = run.start(on_config_update=_throw_once("boom-config-update", calls))
    # The init install was the first (throwing) invocation.
    assert len(calls) == 1
    assert wait_for(lambda: c.get(T9_KEY) == "v3", 5.0), c.get(T9_KEY)
    assert wait_for(lambda: c.connection_state() == "connected", 2.0)
    d: Dict[str, Any] = c._chaos_diagnostics()
    assert d["callback_errors"] == {"on_config_update": 1, "on_connection_state_change": 0}
    assert d["worker_restart_layer1"] == 0
    assert len(calls) == 3, "callback did not run again on the next installs"
    assert c.last_successful_refresh() is not None
    logs = _error_logs_with_trace(records, "boom-config-update")
    assert len(logs) == 1, [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    assert "onConfigUpdate" in logs[0] or "callback" in logs[0]


def test_on_connection_state_change_throw_is_a_callback_error_not_a_restart(
    run: _Run, records: List[logging.LogRecord]
) -> None:
    calls: List[Any] = []
    c = run.start(on_sse_connection_state_change=_throw_once("boom-state-change", calls))
    assert wait_for(lambda: c.get(T9_KEY) == "v3", 5.0), c.get(T9_KEY)
    assert wait_for(lambda: c.connection_state() == "connected", 2.0)
    time.sleep(0.1)
    d = c._chaos_diagnostics()
    assert d["callback_errors"] == {"on_config_update": 0, "on_connection_state_change": 1}
    assert d["worker_restart_layer1"] == 0
    states = [a[0] for a in calls]
    assert states[:2] == ["connecting", "connected"], states
    logs = _error_logs_with_trace(records, "boom-state-change")
    assert len(logs) == 1, [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    assert "callback" in logs[0]
