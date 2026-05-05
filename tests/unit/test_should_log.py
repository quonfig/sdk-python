"""Unit tests for Quonfig.should_log — parallels sdk-node's test/logger-path.test.ts
and sdk-ruby's test/test_should_log.rb.

Covers:
  - logger_key init option surface
  - should_log(logger_path=..., ...) convenience — context injection
  - should_log(config_key=..., ...) primitive — no auto-prefix
  - raises when logger_path is used without logger_key
  - logger_path pass-through verbatim (no normalization)
  - BoundQuonfig inherits logger_key and merges bound contexts
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from quonfig import Quonfig

QUONFIG_SDK_LOGGING_CONTEXT_NAME = "quonfig-sdk-logging"
LOG_LEVEL_KEY = "log-level.my-app"


# --------------------------------------------------------------------------
# Datadir helpers — write a real workspace the SDK can load.
# --------------------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")


def _make_log_level_config(
    key: str,
    *,
    env_rules: List[Dict[str, Any]],
    default_level: str = "warn",
) -> Dict[str, Any]:
    return {
        "id": f"{key}-id",
        "key": key,
        "type": "log_level",
        "valueType": "log_level",
        "sendToClientSdk": False,
        "default": {
            "rules": [
                {
                    "criteria": [{"operator": "ALWAYS_TRUE"}],
                    "value": {"type": "log_level", "value": default_level},
                }
            ]
        },
        "environments": [
            {"id": "Production", "rules": env_rules},
        ],
    }


def _build_datadir(tmp_path: Path, *, log_configs: List[Dict[str, Any]]) -> str:
    datadir = tmp_path / "workspace"
    datadir.mkdir()
    _write_json(datadir / "quonfig.json", {"environments": ["Production"]})
    log_dir = datadir / "log-levels"
    log_dir.mkdir()
    for cfg in log_configs:
        _write_json(log_dir / f"{cfg['key']}.json", cfg)
    return str(datadir)


def _simple_log_level_datadir(tmp_path: Path) -> str:
    """One config, log-level.my-app, fixed 'warn' result regardless of context."""
    cfg = _make_log_level_config(
        LOG_LEVEL_KEY,
        default_level="warn",
        env_rules=[
            {
                "criteria": [{"operator": "ALWAYS_TRUE"}],
                "value": {"type": "log_level", "value": "warn"},
            },
        ],
    )
    return _build_datadir(tmp_path, log_configs=[cfg])


def _per_logger_datadir(tmp_path: Path) -> str:
    """Config with per-logger rules keyed off quonfig-sdk-logging.key."""
    cfg = _make_log_level_config(
        "log-level.test-app",
        default_level="info",
        env_rules=[
            {
                "criteria": [
                    {
                        "propertyName": f"{QUONFIG_SDK_LOGGING_CONTEXT_NAME}.key",
                        "operator": "PROP_STARTS_WITH_ONE_OF",
                        "valueToMatch": {"type": "string_list", "value": ["foo."]},
                    }
                ],
                "value": {"type": "log_level", "value": "debug"},
            },
            {
                "criteria": [
                    {
                        "propertyName": f"{QUONFIG_SDK_LOGGING_CONTEXT_NAME}.key",
                        "operator": "PROP_STARTS_WITH_ONE_OF",
                        "valueToMatch": {"type": "string_list", "value": ["noisy."]},
                    }
                ],
                "value": {"type": "log_level", "value": "error"},
            },
            {
                "criteria": [{"operator": "ALWAYS_TRUE"}],
                "value": {"type": "log_level", "value": "info"},
            },
        ],
    )
    return _build_datadir(tmp_path, log_configs=[cfg])


# --------------------------------------------------------------------------
# logger_key option surface
# --------------------------------------------------------------------------


def test_logger_key_defaults_to_none(tmp_path):
    dd = _simple_log_level_datadir(tmp_path)
    c = Quonfig(datadir=dd, environment="Production").init()
    assert c.logger_key is None


def test_logger_key_accepts_value(tmp_path):
    dd = _simple_log_level_datadir(tmp_path)
    c = Quonfig(datadir=dd, environment="Production", logger_key=LOG_LEVEL_KEY).init()
    assert c.logger_key == LOG_LEVEL_KEY


# --------------------------------------------------------------------------
# should_log(logger_path=...) — requires logger_key
# --------------------------------------------------------------------------


def test_should_log_raises_when_logger_path_used_without_logger_key(tmp_path):
    dd = _simple_log_level_datadir(tmp_path)
    c = Quonfig(datadir=dd, environment="Production").init()
    with pytest.raises(Exception) as exc_info:
        c.should_log(logger_path="MyApp.Foo", desired_level="info")
    assert "logger_key" in str(exc_info.value)


def test_should_log_raises_when_both_config_key_and_logger_path_passed(tmp_path):
    dd = _simple_log_level_datadir(tmp_path)
    c = Quonfig(datadir=dd, environment="Production", logger_key=LOG_LEVEL_KEY).init()
    with pytest.raises(Exception):
        c.should_log(
            config_key=LOG_LEVEL_KEY,
            logger_path="MyApp.Foo",
            desired_level="info",
        )


def test_should_log_raises_when_neither_config_key_nor_logger_path_passed(tmp_path):
    dd = _simple_log_level_datadir(tmp_path)
    c = Quonfig(datadir=dd, environment="Production", logger_key=LOG_LEVEL_KEY).init()
    with pytest.raises(Exception):
        c.should_log(desired_level="info")


# --------------------------------------------------------------------------
# should_log(logger_path=...) — gating semantics + per-logger rules
# --------------------------------------------------------------------------


def test_should_log_per_logger_rules_via_injected_context(tmp_path):
    """foo.bar -> debug rule; noisy.thing -> error rule; other -> info default."""
    dd = _per_logger_datadir(tmp_path)
    c = Quonfig(
        datadir=dd,
        environment="Production",
        logger_key="log-level.test-app",
    ).init()

    # foo.bar matches debug rule → debug emits debug/info/warn/error/fatal, not trace
    assert c.should_log(logger_path="foo.bar", desired_level="debug") is True
    assert c.should_log(logger_path="foo.bar", desired_level="info") is True
    assert c.should_log(logger_path="foo.bar", desired_level="trace") is False

    # noisy.thing matches error rule → does NOT emit info
    assert c.should_log(logger_path="noisy.thing", desired_level="info") is False
    assert c.should_log(logger_path="noisy.thing", desired_level="error") is True

    # otherwise default rule → info, does NOT emit debug
    assert c.should_log(logger_path="other.thing", desired_level="debug") is False
    assert c.should_log(logger_path="other.thing", desired_level="info") is True


def test_should_log_logger_path_passes_through_verbatim(tmp_path):
    """A Ruby-style "MyApp::Services::Auth" path must reach the matcher unchanged."""
    cfg = _make_log_level_config(
        "log-level.native",
        default_level="warn",
        env_rules=[
            {
                "criteria": [
                    {
                        "propertyName": f"{QUONFIG_SDK_LOGGING_CONTEXT_NAME}.key",
                        "operator": "PROP_IS_ONE_OF",
                        "valueToMatch": {
                            "type": "string_list",
                            "value": ["MyApp::Services::Auth"],
                        },
                    }
                ],
                "value": {"type": "log_level", "value": "debug"},
            },
            {
                "criteria": [{"operator": "ALWAYS_TRUE"}],
                "value": {"type": "log_level", "value": "warn"},
            },
        ],
    )
    dd = _build_datadir(tmp_path, log_configs=[cfg])
    c = Quonfig(datadir=dd, environment="Production", logger_key="log-level.native").init()

    # Exact unnormalized path → debug rule → info emits
    assert c.should_log(logger_path="MyApp::Services::Auth", desired_level="info") is True

    # What a normalizing SDK might send → does NOT match → warn default → info NOT emitted
    assert c.should_log(logger_path="my_app.services.auth", desired_level="info") is False


# --------------------------------------------------------------------------
# should_log(config_key=...) — primitive, no auto-prefix
# --------------------------------------------------------------------------


def test_should_log_config_key_no_auto_prefix(tmp_path):
    """Must use the full stored key 'log-level.raw' — SDK does not prepend 'log-level.'."""
    cfg = _make_log_level_config(
        "log-level.raw",
        default_level="info",
        env_rules=[
            {
                "criteria": [{"operator": "ALWAYS_TRUE"}],
                "value": {"type": "log_level", "value": "info"},
            }
        ],
    )
    dd = _build_datadir(tmp_path, log_configs=[cfg])
    c = Quonfig(datadir=dd, environment="Production").init()

    # Full key → info emits info but not debug
    assert c.should_log(config_key="log-level.raw", desired_level="info") is True
    assert c.should_log(config_key="log-level.raw", desired_level="debug") is False

    # Bare name should NOT resolve (no auto-prefix); missing → default true
    assert c.should_log(config_key="raw", desired_level="debug") is True


# --------------------------------------------------------------------------
# Missing config → log everything (match go/node/ruby)
# --------------------------------------------------------------------------


def test_should_log_returns_true_when_no_config_found(tmp_path):
    dd = _simple_log_level_datadir(tmp_path)
    c = Quonfig(
        datadir=dd,
        environment="Production",
        logger_key="log-level.does-not-exist",
    ).init()
    assert c.should_log(logger_path="MyApp.Foo", desired_level="trace") is True


# --------------------------------------------------------------------------
# BoundQuonfig support
# --------------------------------------------------------------------------


def test_bound_client_should_log_uses_logger_path(tmp_path):
    dd = _per_logger_datadir(tmp_path)
    c = Quonfig(
        datadir=dd,
        environment="Production",
        logger_key="log-level.test-app",
    ).init()

    bound = c.with_context({"user": {"id": "u1"}})
    # foo.bar → debug rule
    assert bound.should_log(logger_path="foo.bar", desired_level="debug") is True
    # other → info default, does not emit debug
    assert bound.should_log(logger_path="other.x", desired_level="debug") is False


def test_bound_client_should_log_config_key_form(tmp_path):
    cfg = _make_log_level_config(
        "log-level.raw",
        default_level="info",
        env_rules=[
            {
                "criteria": [{"operator": "ALWAYS_TRUE"}],
                "value": {"type": "log_level", "value": "info"},
            }
        ],
    )
    dd = _build_datadir(tmp_path, log_configs=[cfg])
    c = Quonfig(datadir=dd, environment="Production").init()
    bound = c.with_context({"user": {"id": "u1"}})

    assert bound.should_log(config_key="log-level.raw", desired_level="info") is True
    assert bound.should_log(config_key="log-level.raw", desired_level="debug") is False
