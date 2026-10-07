"""Unit tests for the chaos harness expression evaluator (qfg-goi1.2.22).

The harness itself (``chaos/test_chaos.py``) only runs under
``scripts/run-chaos.sh``; its evaluator is pure, so it is tested here where the
default ``pytest`` run (and CI) picks it up. Mirrors sdk-go
``chaos_expr_test.go``.
"""

from __future__ import annotations

from chaos.test_chaos import ChaosProbe, _evaluate


def _no_server_metric(_name: str) -> float:
    return 0.0


def test_unknown_sdk_metric_fails_loudly() -> None:
    probe = ChaosProbe()
    ok, why = _evaluate(
        "client.sdkMetric('quonfig_no_such_metric_total') == 0", probe, _no_server_metric
    )
    assert ok is False
    assert "unknown sdkMetric" in why
    assert "quonfig_no_such_metric_total" in why


def test_known_sdk_metric_still_evaluates() -> None:
    probe = ChaosProbe()
    ok, why = _evaluate(
        "client.sdkMetric('quonfig_sse_connect_attempts_total') == 0", probe, _no_server_metric
    )
    assert ok is True, why
    ok, why = _evaluate(
        "client.sdkMetric('quonfig_sdk_worker_restart_total', layer='1') == 0",
        probe,
        _no_server_metric,
    )
    assert ok is True, why


def test_unrecognized_expression_fails() -> None:
    ok, _ = _evaluate("client.somethingNew() == 1", ChaosProbe(), _no_server_metric)
    assert ok is False
