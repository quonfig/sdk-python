"""Tests for QUONFIG_DOMAIN env var resolution.

A single ``QUONFIG_DOMAIN`` env var governs the api/sse/telemetry URL
defaults across all Quonfig SDKs. Resolution order (highest wins):

    1. Explicit kwarg (``api_urls=`` / ``telemetry_url=``)
    2. ``QUONFIG_DOMAIN`` env var → derives api + telemetry defaults
    3. Hardcoded default ``"quonfig.com"``

The previously-supported ``QUONFIG_API_URL``, ``QUONFIG_API_URLS``, and
``QUONFIG_TELEMETRY_URL`` env vars have been removed (alpha-phase, no
backward compatibility).
"""

from __future__ import annotations

import pytest

from quonfig import Quonfig
from quonfig.transport import derive_stream_url


@pytest.fixture(autouse=True)
def _clear_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe every URL/domain env var so each test starts clean."""
    for var in (
        "QUONFIG_DOMAIN",
        "QUONFIG_API_URL",
        "QUONFIG_API_URLS",
        "QUONFIG_TELEMETRY_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _client() -> Quonfig:
    return Quonfig(
        sdk_key="sk-test",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )


def test_default_domain_is_quonfig_com() -> None:
    client = _client()
    assert client._api_urls == [
        "https://primary.quonfig.com",
        "https://secondary.quonfig.com",
    ]
    assert client._telemetry_url == "https://telemetry.quonfig.com"


def test_quonfig_domain_drives_all_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUONFIG_DOMAIN", "quonfig-staging.com")
    client = _client()
    assert client._api_urls == [
        "https://primary.quonfig-staging.com",
        "https://secondary.quonfig-staging.com",
    ]
    assert client._telemetry_url == "https://telemetry.quonfig-staging.com"
    # Stream URL is derived from api_urls — sanity-check the chain works
    # end-to-end so a misconfigured staging deploy doesn't silently SSE to prod.
    assert derive_stream_url(client._api_urls[0]) == "https://stream.primary.quonfig-staging.com"


def test_explicit_api_urls_kwarg_overrides_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUONFIG_DOMAIN", "quonfig-staging.com")
    client = Quonfig(
        sdk_key="sk-test",
        api_urls=["https://custom.local"],
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    # Explicit api_urls wins — telemetry still falls through QUONFIG_DOMAIN.
    assert client._api_urls == ["https://custom.local"]
    assert client._telemetry_url == "https://telemetry.quonfig-staging.com"


def test_explicit_telemetry_url_kwarg_overrides_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QUONFIG_DOMAIN", "quonfig-staging.com")
    client = Quonfig(
        sdk_key="sk-test",
        telemetry_url="https://telemetry.local",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    assert client._telemetry_url == "https://telemetry.local"
    # api_urls still derive from QUONFIG_DOMAIN.
    assert client._api_urls == [
        "https://primary.quonfig-staging.com",
        "https://secondary.quonfig-staging.com",
    ]


def test_quonfig_api_url_env_no_longer_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removed env vars must NOT affect resolution."""
    monkeypatch.setenv("QUONFIG_API_URL", "https://legacy.example")
    monkeypatch.setenv("QUONFIG_API_URLS", "https://legacy-plural.example")
    monkeypatch.setenv("QUONFIG_TELEMETRY_URL", "https://legacy-telemetry.example")
    client = _client()
    assert client._api_urls == [
        "https://primary.quonfig.com",
        "https://secondary.quonfig.com",
    ]
    assert client._telemetry_url == "https://telemetry.quonfig.com"
