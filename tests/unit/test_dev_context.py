"""Tests for dev-context injection from ~/.quonfig/tokens.json.

Mirrors sdk-node (devContext.ts), sdk-go (dev_context.go), and sdk-ruby
(dev_context.rb). When ``enable_quonfig_user_context=True`` (or env var
``QUONFIG_DEV_CONTEXT=true``), the SDK reads the per-domain tokens file
written by ``qfg login`` and merges ``{"quonfig-user": {"email": ...}}``
into the global context. Customer-supplied keys win on collision.

``QUONFIG_CONFIG_HOME`` overrides the ``~/.quonfig`` directory for test
isolation. ``QUONFIG_DOMAIN`` drives the tokens filename: production
(quonfig.com) uses ``tokens.json``; any other domain uses a suffixed
name like ``tokens-quonfig-staging-com.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quonfig import Quonfig
from quonfig.dev_context import load_quonfig_user_context, token_filename_for_api_urls


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "QUONFIG_DEV_CONTEXT",
        "QUONFIG_CONFIG_HOME",
        "QUONFIG_DOMAIN",
        "QUONFIG_SDK_KEY",
        "QUONFIG_BACKEND_SDK_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def tokens_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point QUONFIG_CONFIG_HOME at a fresh temp dir and return the
    .quonfig subdirectory that callers should write tokens files into."""
    home = tmp_path / ".quonfig"
    home.mkdir()
    monkeypatch.setenv("QUONFIG_CONFIG_HOME", str(tmp_path))
    return home


def _write_tokens(home: Path, contents: dict, *, filename: str = "tokens.json") -> None:
    (home / filename).write_text(json.dumps(contents))


def _empty_client(**kwargs) -> Quonfig:
    return Quonfig(
        sdk_key="sk-test",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        **kwargs,
    )


# --- token_filename_for_api_urls ---


def test_token_filename_default_is_tokens_json() -> None:
    assert token_filename_for_api_urls(None) == "tokens.json"
    assert token_filename_for_api_urls([]) == "tokens.json"
    assert token_filename_for_api_urls(["https://primary.quonfig.com"]) == "tokens.json"


def test_token_filename_staging_is_suffixed() -> None:
    assert (
        token_filename_for_api_urls(["https://primary.quonfig-staging.com"])
        == "tokens-quonfig-staging-com.json"
    )


def test_token_filename_strips_app_prefix() -> None:
    assert (
        token_filename_for_api_urls(["https://app.quonfig-staging.com"])
        == "tokens-quonfig-staging-com.json"
    )


def test_token_filename_unparseable_falls_back() -> None:
    assert token_filename_for_api_urls(["not a url"]) == "tokens.json"


# --- load_quonfig_user_context (unit) ---


def test_load_returns_email_when_file_present(tokens_home: Path) -> None:
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})

    ctx = load_quonfig_user_context()

    assert ctx == {"quonfig-user": {"email": "bob@foo.com"}}


def test_load_returns_none_when_file_missing(tokens_home: Path) -> None:
    assert load_quonfig_user_context() is None


def test_load_returns_none_when_no_user_email(tokens_home: Path) -> None:
    _write_tokens(tokens_home, {"accessToken": "x"})

    assert load_quonfig_user_context() is None


def test_load_returns_none_when_unparseable(
    tokens_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tokens_home / "tokens.json").write_text("{not valid json")

    with caplog.at_level("WARNING", logger="quonfig.dev_context"):
        result = load_quonfig_user_context()

    assert result is None
    assert any("dev-context" in r.getMessage() for r in caplog.records)


def test_load_picks_staging_file_for_staging_domain(tokens_home: Path) -> None:
    # Production file present too — must NOT be read when domain is staging.
    _write_tokens(tokens_home, {"userEmail": "prod@foo.com"})
    _write_tokens(
        tokens_home,
        {"userEmail": "stg@foo.com"},
        filename="tokens-quonfig-staging-com.json",
    )

    ctx = load_quonfig_user_context(api_urls=["https://primary.quonfig-staging.com"])

    assert ctx == {"quonfig-user": {"email": "stg@foo.com"}}


# --- Quonfig() integration ---


def test_quonfig_injects_email_when_option_enabled(tokens_home: Path) -> None:
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})

    client = _empty_client(enable_quonfig_user_context=True)

    assert client._global_context == {"quonfig-user": {"email": "bob@foo.com"}}


def test_quonfig_enabled_by_default(tokens_home: Path) -> None:
    # The flip: with no opt-in (no enable_quonfig_user_context, no
    # QUONFIG_DEV_CONTEXT) the token file alone triggers injection, and
    # customer context is preserved alongside it.
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})

    customer_ctx = {"user": {"plan": "pro"}}
    client = _empty_client(global_context=customer_ctx)

    assert client._global_context == {
        "user": {"plan": "pro"},
        "quonfig-user": {"email": "bob@foo.com"},
    }


def test_quonfig_option_false_disables(tokens_home: Path) -> None:
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})

    client = _empty_client(enable_quonfig_user_context=False)

    assert "quonfig-user" not in client._global_context


def test_quonfig_env_false_disables(tokens_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})
    monkeypatch.setenv("QUONFIG_DEV_CONTEXT", "false")

    client = _empty_client()

    assert "quonfig-user" not in client._global_context


def test_quonfig_option_true_overrides_env_false(
    tokens_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})
    monkeypatch.setenv("QUONFIG_DEV_CONTEXT", "false")

    client = _empty_client(enable_quonfig_user_context=True)

    assert client._global_context == {"quonfig-user": {"email": "bob@foo.com"}}


def test_quonfig_env_var_enables(tokens_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})
    monkeypatch.setenv("QUONFIG_DEV_CONTEXT", "true")

    client = _empty_client()

    assert client._global_context == {"quonfig-user": {"email": "bob@foo.com"}}


def test_quonfig_customer_context_wins_on_collision(tokens_home: Path) -> None:
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})

    customer_ctx = {"quonfig-user": {"email": "override@x.com"}}
    client = _empty_client(
        enable_quonfig_user_context=True,
        global_context=customer_ctx,
    )

    assert client._global_context == {"quonfig-user": {"email": "override@x.com"}}


def test_quonfig_no_op_when_file_missing(tokens_home: Path) -> None:
    # No tokens.json written.
    customer_ctx = {"user": {"plan": "pro"}}
    client = _empty_client(
        enable_quonfig_user_context=True,
        global_context=customer_ctx,
    )

    assert client._global_context == {"user": {"plan": "pro"}}


def test_quonfig_boots_without_sdk_key_when_tokens_present(
    tokens_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline acceptance: ``Quonfig()`` constructs and reaches an
    initialized state without ``sdk_key`` or ``QUONFIG_BACKEND_SDK_KEY``
    when ``~/.quonfig/tokens.json`` exists and dev-context is enabled."""
    _write_tokens(tokens_home, {"userEmail": "bob@foo.com"})
    monkeypatch.setenv("QUONFIG_DEV_CONTEXT", "true")

    client = Quonfig(
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )

    assert client._global_context == {"quonfig-user": {"email": "bob@foo.com"}}
    # No transport should be wired up (no datadir + no sdk_key).
    assert client._transport is None
    # Init should complete synchronously into an empty store.
    client.init()
    assert client._initialized.is_set()


def test_quonfig_uses_staging_tokens_file_when_domain_set(
    tokens_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_tokens(tokens_home, {"userEmail": "prod@foo.com"})
    _write_tokens(
        tokens_home,
        {"userEmail": "stg@foo.com"},
        filename="tokens-quonfig-staging-com.json",
    )
    monkeypatch.setenv("QUONFIG_DOMAIN", "quonfig-staging.com")

    client = _empty_client(enable_quonfig_user_context=True)

    assert client._global_context == {"quonfig-user": {"email": "stg@foo.com"}}
