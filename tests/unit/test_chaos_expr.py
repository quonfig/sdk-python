"""Unit tests for the chaos harness expression evaluator (qfg-goi1.2.22, qfg-goi1.1.4).

The harness itself (``chaos/test_chaos.py``) only runs under
``scripts/run-chaos.sh``; its evaluator is pure, so it is tested here where the
default ``pytest`` run (and CI) picks it up. Mirrors sdk-go
``chaos_expr_test.go``.
"""

from __future__ import annotations

from chaos.test_chaos import SERVER_METRIC_SKIP_REASON, ChaosProbe, _evaluate


def test_unknown_sdk_metric_fails_loudly() -> None:
    probe = ChaosProbe()
    r = _evaluate("client.sdkMetric('quonfig_no_such_metric_total') == 0", probe)
    assert r.status == "fail"
    assert "unknown sdkMetric" in r.reason
    assert "quonfig_no_such_metric_total" in r.reason


def test_known_sdk_metric_still_evaluates() -> None:
    probe = ChaosProbe()
    r = _evaluate("client.sdkMetric('quonfig_sse_connect_attempts_total') == 0", probe)
    assert r.status == "pass", r.reason
    r = _evaluate("client.sdkMetric('quonfig_sdk_worker_restart_total', layer='1') == 0", probe)
    assert r.status == "pass", r.reason


def test_unrecognized_expression_fails() -> None:
    r = _evaluate("client.somethingNew() == 1", ChaosProbe())
    assert r.status == "fail"


# ----- server_metric: explicit SKIP, never a silent 0 (qfg-goi1.1.4, Decision 6) -----


def test_server_metric_leaf_is_skipped_with_reason() -> None:
    r = _evaluate("server_metric('quonfig_subscriber_lag_seconds') == 0", ChaosProbe())
    assert r.status == "skip"
    assert r.skipped == [r.reason]
    assert "server_metric(quonfig_subscriber_lag_seconds) == 0" in r.reason
    assert SERVER_METRIC_SKIP_REASON in r.reason


def test_skipped_leaf_is_neutral_in_and() -> None:
    # The other leaf is still enforced: a fresh probe is "disconnected", so
    # requiring "connected" must fail even though the server_metric leaf skips.
    probe = ChaosProbe()
    r = _evaluate(
        "client.connectionState() == 'connected' "
        "AND server_metric('quonfig_subscriber_lag_seconds') == 0",
        probe,
    )
    assert r.status == "fail"
    assert "connectionState=" in r.reason
    assert len(r.skipped) == 1
    with probe.lock:
        probe.conn_state = "connected"
    r = _evaluate(
        "client.connectionState() == 'connected' "
        "AND server_metric('quonfig_subscriber_lag_seconds') == 0",
        probe,
    )
    assert r.status == "pass", r.reason
    assert len(r.skipped) == 1


def test_skipped_leaf_is_neutral_in_or() -> None:
    probe = ChaosProbe()
    r = _evaluate(
        "server_metric('quonfig_subscriber_lag_seconds') == 0 "
        "OR client.connectionState() == 'connected'",
        probe,
    )
    assert r.status == "fail"
    assert len(r.skipped) == 1
    with probe.lock:
        probe.conn_state = "connected"
    r = _evaluate(
        "server_metric('quonfig_subscriber_lag_seconds') == 0 "
        "OR client.connectionState() == 'connected'",
        probe,
    )
    assert r.status == "pass", r.reason


def test_all_leaves_skipped_is_skipped() -> None:
    r = _evaluate(
        "server_metric('a') == 0 AND server_metric('b') >= 1",
        ChaosProbe(),
    )
    assert r.status == "skip"
    assert len(r.skipped) == 2
