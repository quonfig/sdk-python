"""Tests for SDK-key resolution from the environment.

A bare ``Quonfig()`` (no ``sdk_key=`` kwarg) auto-loads the key from the
``QUONFIG_BACKEND_SDK_KEY`` env var — the same var every other Quonfig SDK
(go, node, ruby, java) and the ``qfg run`` CLI read. Resolution order:

    1. Explicit ``sdk_key=`` kwarg
    2. ``QUONFIG_BACKEND_SDK_KEY`` env var

The legacy ``QUONFIG_SDK_KEY`` name was a python-only outlier (never
documented — the docs always said ``QUONFIG_BACKEND_SDK_KEY``) and is no
longer read (alpha-phase, no backward compatibility — mirrors the
``QUONFIG_API_URL`` removal in ``test_domain_env.py``).
"""

from __future__ import annotations

import pytest

from quonfig import Quonfig


@pytest.fixture(autouse=True)
def _clear_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe every SDK-key env var so each test starts clean."""
    for var in ("QUONFIG_BACKEND_SDK_KEY", "QUONFIG_SDK_KEY"):
        monkeypatch.delenv(var, raising=False)


def _client() -> Quonfig:
    return Quonfig(
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )


def test_backend_sdk_key_env_is_autoloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUONFIG_BACKEND_SDK_KEY", "sk-from-backend-env")
    assert _client()._sdk_key == "sk-from-backend-env"


def test_explicit_kwarg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUONFIG_BACKEND_SDK_KEY", "sk-from-env")
    client = Quonfig(
        sdk_key="sk-explicit",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    assert client._sdk_key == "sk-explicit"


def test_legacy_quonfig_sdk_key_no_longer_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The removed legacy var must NOT resolve a key."""
    monkeypatch.setenv("QUONFIG_SDK_KEY", "sk-legacy-should-be-ignored")
    assert _client()._sdk_key == ""
