"""Explicit single-URL ``api_urls`` disables failover -> WARN once at init.

The default (and every ``QUONFIG_DOMAIN``-derived) ``api_urls`` list carries a
primary and a secondary leg, and the SDK hedges/fails over between them. An
explicit ``api_urls=`` with a single entry silently drops the secondary, so the
SDK logs a one-line WARNING at construction pointing the caller at the fix
(pass both a primary and a secondary URL). Mirrors sdk-go (qfg-41nh.26).
"""

from __future__ import annotations

import logging

from quonfig import Quonfig

_FRAGMENT = "explicit api_urls disables automatic failover"


def _failover_warnings(caplog) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records if r.levelno == logging.WARNING and _FRAGMENT in r.getMessage()
    ]


def test_single_explicit_api_url_warns_failover_lost(monkeypatch, caplog) -> None:
    """One explicit api_urls entry drops the secondary -> exactly one WARN."""
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)

    with caplog.at_level(logging.WARNING, logger="quonfig.client"):
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=["https://primary.example.test"],
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            fallback_poll_enabled=False,
            init_timeout_ms=2000,
            on_init_failure="return_zero_value",
        )
        client.close()

    warnings = _failover_warnings(caplog)
    assert len(warnings) == 1, f"expected exactly one failover-lost WARN, got {warnings!r}"
    msg = warnings[0].getMessage()
    assert "primary and secondary" in msg


def test_two_explicit_api_urls_do_not_warn(monkeypatch, caplog) -> None:
    """Two explicit URLs keep failover -> no WARN."""
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)

    with caplog.at_level(logging.WARNING, logger="quonfig.client"):
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=[
                "https://primary.example.test",
                "https://secondary.example.test",
            ],
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            fallback_poll_enabled=False,
            init_timeout_ms=2000,
            on_init_failure="return_zero_value",
        )
        client.close()

    assert _failover_warnings(caplog) == [], "two explicit URLs keep failover and must not warn"


def test_default_api_urls_do_not_warn(monkeypatch, caplog) -> None:
    """No explicit api_urls -> the derived two-leg list keeps failover, no WARN."""
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)

    with caplog.at_level(logging.WARNING, logger="quonfig.client"):
        client = Quonfig(
            sdk_key="sdk-test",
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            fallback_poll_enabled=False,
            init_timeout_ms=2000,
            on_init_failure="return_zero_value",
        )
        client.close()

    assert _failover_warnings(caplog) == [], (
        "default QUONFIG_DOMAIN-derived list carries both legs and must not warn"
    )
