"""Unit tests for ``QuonfigLoggerFilter`` — the stdlib ``logging.Filter`` adapter.

Covers:
  - records above/at/below the configured level gate correctly
  - logger path flows into the Quonfig-logging context verbatim
  - filter with no Quonfig client passes everything through (defensive default)
  - per-logger rules route based on the record's ``name``
  - evaluator exceptions don't mask logs
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from quonfig import Quonfig, QuonfigLoggerFilter

QUONFIG_SDK_LOGGING_CONTEXT_NAME = "quonfig-sdk-logging"


# --- datadir helpers (mirrors tests/unit/test_should_log.py) ----------------


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
        "environments": [{"id": "Production", "rules": env_rules}],
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


def _fixed_level_datadir(tmp_path: Path, level: str) -> str:
    """A single log-level.my-app config that always resolves to ``level``."""
    cfg = _make_log_level_config(
        "log-level.my-app",
        default_level=level,
        env_rules=[
            {
                "criteria": [{"operator": "ALWAYS_TRUE"}],
                "value": {"type": "log_level", "value": level},
            }
        ],
    )
    return _build_datadir(tmp_path, log_configs=[cfg])


def _per_logger_datadir(tmp_path: Path) -> str:
    """Per-logger rules: foo.* -> debug, noisy.* -> error, else info."""
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


# --- tests -----------------------------------------------------------------


def _make_record(name: str, level: int, msg: str) -> logging.LogRecord:
    """Build a LogRecord by hand — avoids touching global loggers."""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=None,
        exc_info=None,
    )


def test_filter_gates_below_configured_level(tmp_path):
    """logger_key resolves to INFO → DEBUG records dropped, INFO+ emit."""
    dd = _fixed_level_datadir(tmp_path, "info")
    client = Quonfig(datadir=dd, environment="Production", logger_key="log-level.my-app").init()
    f = QuonfigLoggerFilter(client)

    assert f.filter(_make_record("my.app", logging.DEBUG, "dbg")) is False
    assert f.filter(_make_record("my.app", logging.INFO, "inf")) is True
    assert f.filter(_make_record("my.app", logging.WARNING, "warn")) is True
    assert f.filter(_make_record("my.app", logging.ERROR, "err")) is True


def test_filter_passes_through_when_client_is_none():
    f = QuonfigLoggerFilter(None)
    assert f.filter(_make_record("anything", logging.DEBUG, "x")) is True


def test_filter_per_logger_rules_use_record_name_verbatim(tmp_path):
    """record.name is passed to should_log verbatim, driving per-logger rules."""
    dd = _per_logger_datadir(tmp_path)
    client = Quonfig(
        datadir=dd,
        environment="Production",
        logger_key="log-level.test-app",
    ).init()
    f = QuonfigLoggerFilter(client)

    # foo.bar matches debug rule → DEBUG emits
    assert f.filter(_make_record("foo.bar", logging.DEBUG, "d")) is True
    # noisy.thing matches error rule → INFO does NOT emit
    assert f.filter(_make_record("noisy.thing", logging.INFO, "i")) is False
    assert f.filter(_make_record("noisy.thing", logging.ERROR, "e")) is True
    # other → info default → DEBUG does not emit
    assert f.filter(_make_record("other.thing", logging.DEBUG, "d")) is False
    assert f.filter(_make_record("other.thing", logging.INFO, "i")) is True


def test_filter_logger_path_passes_through_unnormalized(tmp_path):
    """Non-dotted paths (e.g. 'MyApp::Services::Auth') reach the matcher as-is."""
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
    client = Quonfig(datadir=dd, environment="Production", logger_key="log-level.native").init()
    f = QuonfigLoggerFilter(client)

    assert f.filter(_make_record("MyApp::Services::Auth", logging.INFO, "m")) is True
    # Normalized variant is a different string → warn default → INFO does not emit
    assert f.filter(_make_record("my_app.services.auth", logging.INFO, "m")) is False


def test_filter_override_logger_path_constructor_arg(tmp_path):
    """The ``logger_path=`` kwarg forces a fixed path for every record."""
    dd = _per_logger_datadir(tmp_path)
    client = Quonfig(
        datadir=dd,
        environment="Production",
        logger_key="log-level.test-app",
    ).init()
    f = QuonfigLoggerFilter(client, logger_path="foo.bar")

    # Even though the record's name is 'noisy.thing' (which would be error-only),
    # the override forces 'foo.bar' → debug rule → DEBUG emits.
    assert f.filter(_make_record("noisy.thing", logging.DEBUG, "d")) is True


def test_filter_integrates_with_handler_pipeline(tmp_path, caplog):
    """End-to-end: addFilter on a logger drops DEBUG when config is INFO."""
    dd = _fixed_level_datadir(tmp_path, "info")
    client = Quonfig(datadir=dd, environment="Production", logger_key="log-level.my-app").init()

    logger = logging.getLogger("quonfig.test.pipeline")
    logger.setLevel(logging.DEBUG)  # allow DEBUG through to the filter
    logger.addFilter(QuonfigLoggerFilter(client))

    try:
        with caplog.at_level(logging.DEBUG, logger="quonfig.test.pipeline"):
            logger.debug("dropped")
            logger.info("kept")
            logger.error("kept-error")

        messages = [r.message for r in caplog.records]
        assert "dropped" not in messages
        assert "kept" in messages
        assert "kept-error" in messages
    finally:
        # Clean up so other tests aren't affected by the lingering filter.
        logger.filters.clear()


def test_filter_allows_log_when_evaluation_raises(monkeypatch, tmp_path):
    """Exceptions in should_log must not silently drop logs."""
    dd = _fixed_level_datadir(tmp_path, "error")
    client = Quonfig(datadir=dd, environment="Production", logger_key="log-level.my-app").init()

    def boom(*args, **kwargs):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(client, "should_log", boom)

    f = QuonfigLoggerFilter(client)
    assert f.filter(_make_record("x", logging.DEBUG, "m")) is True
