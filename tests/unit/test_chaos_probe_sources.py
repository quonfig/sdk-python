"""Chaos probe reads every asserted value from the SDK (qfg-6vno.5).

The chaos runners (``chaos/test_chaos.py``, ``chaos/test_failover_chaos.py``)
only run under the toxiproxy wrappers, but their probes and evaluators are
pure, so they are tested here against a fake client. Each test pins one value
to its SDK source: a probe that answers from its own bookkeeping (a local
state machine, a constant) fails these.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

import pytest

from chaos import test_chaos as chaos
from chaos import test_failover_chaos as failover


class FakeClient:
    """Stands in for ``Quonfig``: only the surfaces the probe may read."""

    def __init__(
        self,
        state: str = "connected",
        diag: Optional[Dict[str, Any]] = None,
        ready: bool = True,
        strings: Optional[Dict[str, str]] = None,
        fallback: bool = False,
    ) -> None:
        self.state = state
        self.diag = {
            "sse_connect_attempts": 0,
            "worker_restart_layer1": 0,
            "worker_restart_layer2": 0,
            "sse_worker_alive": True,
        }
        self.diag.update(diag or {})
        self.ready_ = ready
        self.strings = strings or {}
        self.fallback = fallback

    def connection_state(self) -> str:
        return self.state

    def fallback_poller_active(self) -> bool:
        return self.fallback

    def last_successful_refresh(self) -> Optional[datetime.datetime]:
        return None

    def _chaos_diagnostics(self) -> Dict[str, Any]:
        return dict(self.diag)

    def is_ready(self) -> bool:
        return self.ready_

    def ready(self) -> bool:
        return self.ready_

    def get_string(self, key: str, default: Any = None) -> Any:
        return self.strings.get(key, default)


def _probe(client: FakeClient) -> "chaos.ChaosProbe":
    p = chaos.ChaosProbe()
    p.set_client(client)
    return p


# ----- connectionState comes from client.connection_state() -----


@pytest.mark.parametrize("state", ["connected", "disconnected", "falling_back", "initializing"])
def test_connection_state_is_the_sdks(state: str) -> None:
    r = chaos._evaluate(f"client.connectionState() == '{state}'", _probe(FakeClient(state=state)))
    assert r.status == "pass", r.reason


def test_connection_state_reconnecting_is_not_a_contract_value() -> None:
    # The supervisor contract fixes the enum; a scenario row asking for an
    # invented value must fail loudly, never be matched by a probe tracker.
    r = chaos._evaluate(
        "client.connectionState() == 'reconnecting'", _probe(FakeClient(state="connected"))
    )
    assert r.status == "fail"
    assert "not a connectionState() value" in r.reason


def test_fallback_poller_active_is_the_sdks() -> None:
    r = chaos._evaluate("client.fallbackPollerActive() == true", _probe(FakeClient(fallback=True)))
    assert r.status == "pass", r.reason


# ----- sdkMetric comes from the SDK's diagnostics hook -----


def test_worker_restart_layer1_is_the_sdks() -> None:
    p = _probe(FakeClient(diag={"worker_restart_layer1": 3}))
    r = chaos._evaluate("client.sdkMetric('quonfig_sdk_worker_restart_total', layer='1') >= 3", p)
    assert r.status == "pass", r.reason
    r = chaos._evaluate("client.sdkMetric('quonfig_sdk_worker_restart_total', layer='1') == 0", p)
    assert r.status == "fail"


def test_connect_attempts_is_the_sdks() -> None:
    p = _probe(FakeClient(diag={"sse_connect_attempts": 7}))
    r = chaos._evaluate("client.sdkMetric('quonfig_sse_connect_attempts_total') == 7", p)
    assert r.status == "pass", r.reason


def test_worker_restart_layer2_is_the_sdks() -> None:
    # Read from the Layer 2 restart supervisor's counter (qfg-11ct), never a
    # constant: a non-zero count must fail 11[4]'s `== 0`.
    p = _probe(FakeClient(diag={"worker_restart_layer2": 2}))
    r = chaos._evaluate("client.sdkMetric('quonfig_sdk_worker_restart_total', layer='2') >= 2", p)
    assert r.status == "pass", r.reason
    r = chaos._evaluate("client.sdkMetric('quonfig_sdk_worker_restart_total', layer='2') == 0", p)
    assert r.status == "fail", r.reason
    r = chaos._evaluate(
        "client.sdkMetric('quonfig_sdk_worker_restart_total', layer='2') == 0",
        _probe(FakeClient()),
    )
    assert r.status == "pass", r.reason


# ----- processStillAlive comes from the SDK's SSE worker -----


def test_process_still_alive_fails_when_sse_worker_died() -> None:
    p = _probe(FakeClient(diag={"sse_worker_alive": False}))
    r = chaos._evaluate("client.processStillAlive() == true", p)
    assert r.status == "fail", r.reason


def test_process_still_alive_passes_with_live_worker() -> None:
    r = chaos._evaluate("client.processStillAlive() == true", _probe(FakeClient()))
    assert r.status == "pass", r.reason


# ----- unknown chaos actions fail the run -----


class _RecordingTp:
    def __init__(self) -> None:
        self.calls: list = []

    def __getattr__(self, name: str) -> Any:
        def _f(*a: Any, **k: Any) -> None:
            self.calls.append((name, a))

        return _f


def test_unknown_process_action_raises() -> None:
    with pytest.raises(ValueError, match="unknown process action"):
        chaos._apply_process(_RecordingTp(), {"action": "kill_everything"})


def test_unknown_inject_shape_raises() -> None:
    with pytest.raises(ValueError, match="unknown inject"):
        chaos._apply_inject(_RecordingTp(), {"name": "x", "sse_teleport_ms": 5})


def test_unsupported_sse_http_status_inject_raises() -> None:
    with pytest.raises(ValueError, match="sse_http_status"):
        chaos._apply_inject(_RecordingTp(), {"name": "x", "sse_http_status": 503})


def test_failover_unknown_inject_shape_raises() -> None:
    with pytest.raises(ValueError, match="unknown inject"):
        failover._apply_inject(_RecordingTp(), {"name": "x", "primary_teleport_ms": 5})


# ----- sdkLog counts lines at exactly the asserted level -----


def test_sdk_log_level_is_exact() -> None:
    p = chaos.ChaosProbe()
    p.log("WARNING", "[quonfig] init slow")
    p.log("ERROR", "[quonfig] init failed")
    assert chaos._evaluate("client.sdkLog('warn', /\\[quonfig\\] init/) == 1", p).ok
    assert chaos._evaluate("client.sdkLog('error', /\\[quonfig\\] init/) == 1", p).ok


def test_failover_sdk_log_vocabulary() -> None:
    p = failover.ChaosProbe()
    p.log("WARNING", "[quonfig] init: primary refused")
    ok, why = failover._evaluate(
        "client.sdkLog('warn', /\\[quonfig\\] init/) == 0 "
        "AND client.sdkLog('error', /\\[quonfig\\] init/) == 0",
        p,
    )
    assert not ok, why
    assert "sdkLog(warn" in why
    ok, why = failover._evaluate("client.sdkLog('error', /\\[quonfig\\] init/) == 0", p)
    assert ok, why


# ----- init-failure vocabulary (scenario 00) -----


def test_init_state_vocabulary() -> None:
    p = _probe(FakeClient())
    assert chaos._evaluate("client.initState() == 'pending'", p).ok
    p.set_init_state("raised")
    assert chaos._evaluate("client.initState() == 'raised'", p).ok
    assert not chaos._evaluate("client.initState() == 'returned'", p).ok


def test_get_string_reads_the_sdk() -> None:
    p = _probe(FakeClient(strings={"config.with.only.default.env.row": "hello from no env row"}))
    assert chaos._evaluate(
        "client.getString('config.with.only.default.env.row', 'CODE-DEFAULT') "
        "== 'hello from no env row'",
        p,
    ).ok
    p2 = _probe(FakeClient())
    assert chaos._evaluate(
        "client.getString('config.with.only.default.env.row', 'CODE-DEFAULT') == 'CODE-DEFAULT'",
        p2,
    ).ok


def test_ready_reads_the_sdk() -> None:
    assert chaos._evaluate("client.ready() == false", _probe(FakeClient(ready=False))).ok
    assert chaos._evaluate("client.ready() == true", _probe(FakeClient(ready=True))).ok


def test_ready_event_count_counts_sdk_callbacks_after_init_returned() -> None:
    p = _probe(FakeClient())
    p.on_config_update()  # fired while init() is still pending: not counted
    assert chaos._evaluate("client.readyEventCount() == 0", p).ok
    p.set_init_state("returned")
    p.on_config_update()
    assert chaos._evaluate("client.readyEventCount() >= 1", p).ok
