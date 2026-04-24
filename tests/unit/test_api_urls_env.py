"""Regression tests for API URL env var resolution.

Other Quonfig SDKs (sdk-node, sdk-ruby, sdk-go, sdk-javascript) read
`QUONFIG_API_URLS` (plural, comma-separated list). The Python SDK must match
that so tester apps can share one env var across SDKs. `QUONFIG_API_URL`
(singular) is a deprecated fallback retained for one release.
"""
from __future__ import annotations

import warnings

import pytest

from quonfig.client import Quonfig, _DEFAULT_API_URL


def test_reads_plural_urls_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUONFIG_API_URL", raising=False)
    monkeypatch.setenv(
        "QUONFIG_API_URLS",
        "https://primary.quonfig.com,https://secondary.quonfig.com",
    )

    client = Quonfig(sdk_key="sk-test", collect_evaluation_summaries=False,
                     context_upload_mode="none")

    assert client._api_urls == [
        "https://primary.quonfig.com",
        "https://secondary.quonfig.com",
    ]


def test_plural_takes_precedence_over_singular(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUONFIG_API_URLS", "https://plural.quonfig.com")
    monkeypatch.setenv("QUONFIG_API_URL", "https://singular.quonfig.com")

    client = Quonfig(sdk_key="sk-test", collect_evaluation_summaries=False,
                     context_upload_mode="none")

    assert client._api_urls == ["https://plural.quonfig.com"]


def test_singular_still_works_as_deprecated_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUONFIG_API_URLS", raising=False)
    monkeypatch.setenv("QUONFIG_API_URL", "https://legacy.quonfig.com")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = Quonfig(sdk_key="sk-test", collect_evaluation_summaries=False,
                         context_upload_mode="none")

    assert client._api_urls == ["https://legacy.quonfig.com"]
    assert any(
        issubclass(w.category, DeprecationWarning)
        and "QUONFIG_API_URLS" in str(w.message)
        for w in caught
    ), f"expected DeprecationWarning mentioning QUONFIG_API_URLS, got {[str(w.message) for w in caught]}"


def test_api_urls_param_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUONFIG_API_URLS", "https://env.quonfig.com")

    client = Quonfig(
        sdk_key="sk-test",
        api_urls=["https://param.quonfig.com"],
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )

    assert client._api_urls == ["https://param.quonfig.com"]


def test_falls_back_to_default_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUONFIG_API_URLS", raising=False)
    monkeypatch.delenv("QUONFIG_API_URL", raising=False)

    client = Quonfig(sdk_key="sk-test", collect_evaluation_summaries=False,
                     context_upload_mode="none")

    assert client._api_urls == [_DEFAULT_API_URL]
