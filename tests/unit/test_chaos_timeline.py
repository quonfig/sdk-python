"""Unit tests for the chaos expectation timeline (qfg-goi1.10.34, qfg-goi1.7).

Pins integration-test-data ``chaos/README.md`` "Timeline rules (every
harness)": ``after_ms`` gating, hold reset, ``within_ms`` failure, shape-based
SKIPPED, strict expectation keys, and the probe reading
``client.last_successful_refresh()``.
"""

from __future__ import annotations

import datetime
from typing import List, Tuple

import pytest

from chaos.test_chaos import ChaosProbe, _evaluate
from chaos.timeline import ExpState, step, validate_expectations


def _const(status: str, reason: str = "r") -> "callable":
    calls: List[int] = []

    def _f() -> Tuple[str, str, List[str]]:
        calls.append(1)
        return status, reason, []

    _f.calls = calls  # type: ignore[attr-defined]
    return _f


# ----- rule 1: after_ms -----


def test_after_ms_true_at_tick0_is_pending_not_pass() -> None:
    s = ExpState(0, {"within_ms": 5000, "after_ms": 1000, "assert": "x"})
    ev = _const("pass")
    step(s, 0, ev)
    assert not s.terminal
    assert s.held_since is None
    assert ev.calls == []  # not evaluated at all before after_ms
    step(s, 999, ev)
    assert not s.terminal and ev.calls == []
    step(s, 1000, ev)
    assert s.passed
    assert s.hit_at == 1000


def test_after_ms_hold_starts_at_or_after_after_ms() -> None:
    s = ExpState(0, {"within_ms": 5000, "after_ms": 1000, "must_hold_for_ms": 500, "assert": "x"})
    ev = _const("pass")
    for t in (0, 250, 500, 750):
        step(s, t, ev)
    assert s.held_since is None
    step(s, 1250, ev)
    assert s.held_since == 1250 and not s.passed
    step(s, 1500, ev)
    assert not s.passed
    step(s, 1750, ev)
    assert s.passed


def test_after_ms_false_after_window_fails_at_within() -> None:
    s = ExpState(0, {"within_ms": 2000, "after_ms": 1000, "assert": "x"})
    ev = _const("fail", "nope")
    step(s, 1500, ev)
    assert not s.terminal
    step(s, 2001, ev)
    assert s.failed and s.last_reason == "nope"


def test_after_ms_server_metric_still_skipped() -> None:
    exp = {
        "within_ms": 230000,
        "after_ms": 185000,
        "assert": "server_metric('quonfig_subscriber_lag_seconds') == 0",
    }
    s = ExpState(0, exp)

    def ev() -> Tuple[str, str, List[str]]:
        r = _evaluate(exp["assert"], ChaosProbe())
        return r.status, r.reason, r.skipped

    step(s, 0, ev, skip_shape=True)
    assert s.skipped


# ----- rule 3: hold resets on a false sample, fails past within_ms -----


def test_hold_resets_on_false_sample_and_fails_past_within() -> None:
    s = ExpState(0, {"within_ms": 1000, "must_hold_for_ms": 500, "assert": "x"})
    step(s, 0, _const("pass"))
    assert s.held_since == 0
    step(s, 250, _const("fail"))
    assert s.held_since is None
    step(s, 600, _const("pass"))
    assert s.held_since == 600 and not s.passed
    step(s, 1001, _const("pass"))
    assert s.failed and not s.passed


def test_no_after_ms_unchanged() -> None:
    s = ExpState(0, {"within_ms": 1000, "assert": "x"})
    step(s, 0, _const("pass"))
    assert s.passed and s.hit_at == 0


# ----- rule 4: strict expectation keys -----


def test_unknown_expectation_key_rejected_naming_key() -> None:
    run = {
        "name": "r",
        "expectations": [{"within_ms": 1000, "not_before_ms": 500, "assert": "x"}],
    }
    with pytest.raises(ValueError, match="not_before_ms"):
        validate_expectations(run, "scenario.yaml")


def test_known_expectation_keys_accepted() -> None:
    run = {
        "name": "r",
        "expectations": [
            {"within_ms": 1000, "after_ms": 10, "must_hold_for_ms": 100, "assert": "x"},
        ],
    }
    validate_expectations(run, "scenario.yaml")


def test_pinned_corpus_loads_with_strict_keys() -> None:
    from chaos import test_chaos, test_failover_chaos

    # Module import already ran every loader; the corpus must be valid.
    assert test_chaos._load_runs is not None
    assert test_failover_chaos._load_runs is not None


# ----- probe rewire: lastSuccessfulRefresh reads the client accessor -----


class _FakeClient:
    def __init__(self) -> None:
        self.stamp = None

    def last_successful_refresh(self):  # noqa: ANN201
        return self.stamp


def test_probe_last_refresh_tracks_client_accessor_without_install() -> None:
    probe = ChaosProbe()
    fake = _FakeClient()
    probe.set_client(fake)  # type: ignore[arg-type]
    # A 304-only refresh advances the client's stamp but fires no on_update.
    fake.stamp = datetime.datetime.now(datetime.timezone.utc)
    r = _evaluate("client.lastSuccessfulRefresh() >= (now() - 70000)", probe)
    assert r.status == "pass", r.reason


def test_probe_last_refresh_none_is_stale() -> None:
    probe = ChaosProbe()
    probe.set_client(_FakeClient())  # type: ignore[arg-type]
    r = _evaluate("client.lastSuccessfulRefresh() >= (now() - 70000)", probe)
    assert r.status == "fail"
