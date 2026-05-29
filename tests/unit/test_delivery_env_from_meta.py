"""SDK-key delivery mode: evaluator env id must come from meta.environment.

api-delivery serializes each config scoped to ONE environment using a SINGULAR
``environment`` block plus ``meta.environment`` = the active env id. In SDK-key
delivery mode the consumer does NOT pin an environment (the server scopes by
key), so the SDK must take the active env from ``meta.environment`` — mirroring
sdk-go's ``c.envID = envelope.Meta.Environment``.

Regression for qfg-xpln.3: previously the evaluator's env id was sourced only
from the client-pinned ``environment=`` / ``QUONFIG_ENVIRONMENT``; with no pin
it was "" so the singular env block (id "development") never matched and eval
silently fell back to ``default``.
"""

from __future__ import annotations

import time

from quonfig import Quonfig
from quonfig.types import (
    ConfigEnvelope,
    ConfigResponse,
    Criterion,
    Environment,
    Meta,
    Rule,
    RuleSet,
    Value,
)


def _always_true(value: Value) -> Rule:
    return Rule(criteria=[Criterion(operator="ALWAYS_TRUE")], value=value)


def _delivery_envelope(active_env: str) -> ConfigEnvelope:
    """One config, wire-shape as api-delivery emits it in SDK-key mode:

    - singular ``environment`` block scoped to ``active_env`` -> value False
    - ``default`` -> value True
    - ``meta.environment`` == active_env
    """
    config = ConfigResponse(
        id="cfg-1",
        key="my.flag",
        type="config",
        value_type="bool",
        send_to_client_sdk=True,
        default=RuleSet(rules=[_always_true(Value(type="bool", value=True))]),
        environment=Environment(
            id=active_env,
            rules=[_always_true(Value(type="bool", value=False))],
        ),
    )
    return ConfigEnvelope(
        configs=[config],
        meta=Meta(version="v1", environment=active_env),
    )


def test_delivery_mode_takes_env_from_meta_when_unpinned(monkeypatch) -> None:
    """No env pinned: env override (False) must win over default (True)."""
    monkeypatch.delenv("QUONFIG_ENVIRONMENT", raising=False)

    client = Quonfig(
        sdk_key="sdk-test",
        api_urls=["http://localhost:0"],
        # no environment= pin, no QUONFIG_ENVIRONMENT
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        fallback_poll_enabled=False,
        init_timeout_ms=2000,
        on_init_failure="return_zero_value",
    )
    try:
        assert client._transport is not None
        monkeypatch.setattr(
            client._transport, "fetch", lambda etag=None: _delivery_envelope("development")
        )
        client.init()
        # Let the background initial-fetch thread install the envelope.
        deadline = time.time() + 2.0
        while time.time() < deadline and client._store.get("my.flag") is None:
            time.sleep(0.01)
        assert client._store.get("my.flag") is not None, "config never installed"

        value = client.get_bool("my.flag", default=None)
        assert value is False, (
            "expected env override (False) from meta.environment='development', "
            f"got {value!r} (env id not derived from meta.environment)"
        )
    finally:
        client.close()


def test_client_pin_takes_precedence_over_meta(monkeypatch) -> None:
    """An explicit environment= pin must still win over meta.environment.

    Here meta.environment is 'staging' (no matching env block on the config),
    but the client pins 'development', which DOES have an env block -> False.
    """
    monkeypatch.delenv("QUONFIG_ENVIRONMENT", raising=False)

    config = ConfigResponse(
        id="cfg-1",
        key="my.flag",
        type="config",
        value_type="bool",
        send_to_client_sdk=True,
        default=RuleSet(rules=[_always_true(Value(type="bool", value=True))]),
        environment=Environment(
            id="development",
            rules=[_always_true(Value(type="bool", value=False))],
        ),
    )
    envelope = ConfigEnvelope(
        configs=[config],
        meta=Meta(version="v1", environment="staging"),
    )

    client = Quonfig(
        sdk_key="sdk-test",
        api_urls=["http://localhost:0"],
        environment="development",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        fallback_poll_enabled=False,
        init_timeout_ms=2000,
        on_init_failure="return_zero_value",
    )
    try:
        assert client._transport is not None
        monkeypatch.setattr(client._transport, "fetch", lambda etag=None: envelope)
        client.init()
        deadline = time.time() + 2.0
        while time.time() < deadline and client._store.get("my.flag") is None:
            time.sleep(0.01)
        assert client._store.get("my.flag") is not None, "config never installed"

        # Pin 'development' matches the env block -> False, despite meta=staging.
        assert client.get_bool("my.flag", default=None) is False
    finally:
        client.close()
