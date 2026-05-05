"""Regression tests for API URL kwarg + default resolution.

Cross-SDK convention: a single ``QUONFIG_DOMAIN`` env var governs the api,
sse, and telemetry URL defaults. The full domain → URL chain is exercised
in ``test_domain_env.py``; this file focuses on the api_urls kwarg path
and default behavior when no env vars are set.
"""

from __future__ import annotations

import pytest

from quonfig import Quonfig


@pytest.fixture(autouse=True)
def _clear_domain_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)


def test_api_urls_param_overrides_default() -> None:
    client = Quonfig(
        sdk_key="sk-test",
        api_urls=["https://param.quonfig.com"],
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )

    assert client._api_urls == ["https://param.quonfig.com"]


def test_falls_back_to_default_when_no_env() -> None:
    client = Quonfig(
        sdk_key="sk-test",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )

    assert client._api_urls == [
        "https://primary.quonfig.com",
        "https://secondary.quonfig.com",
    ]
