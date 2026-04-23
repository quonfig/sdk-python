"""Unit tests for ``QuonfigLoggerProcessor`` — the structlog processor adapter.

Covers:
  - structlog method name & event_dict["level"] map to the right level
  - events above/at/below configured level gate correctly (DropEvent raised)
  - logger path flows into the Quonfig-logging context verbatim
  - processor with no Quonfig client passes events through
  - per-logger rules route based on ``logger.name`` or event_dict["logger"]
  - evaluator exceptions don't mask events
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
import structlog
from structlog import DropEvent

from quonfig import Quonfig, QuonfigLoggerProcessor

QUONFIG_SDK_LOGGING_CONTEXT_NAME = "quonfig-sdk-logging"


# --- datadir helpers -------------------------------------------------------


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


class _NamedLogger:
    """Minimal stand-in for a structlog logger that exposes ``.name``."""

    def __init__(self, name: str) -> None:
        self.name = name


# --- level extraction ------------------------------------------------------


def test_level_extraction_prefers_level_number(tmp_path):
    """If event_dict['level_number'] is an int, we convert it via logging."""
    from quonfig.logging import _level_name_for_structlog

    # 30 == WARNING
    assert _level_name_for_structlog("info", {"level_number": 30}) == "WARNING"
    # 10 == DEBUG
    assert _level_name_for_structlog("warn", {"level_number": 10}) == "DEBUG"


def test_level_extraction_falls_back_to_level_string():
    from quonfig.logging import _level_name_for_structlog

    assert _level_name_for_structlog("info", {"level": "warning"}) == "WARNING"
    # structlog alias: "warn" → WARNING
    assert _level_name_for_structlog("warn", {}) == "WARNING"
    # structlog alias: "exception" → ERROR
    assert _level_name_for_structlog("exception", {}) == "ERROR"


def test_level_extraction_returns_none_for_unknown():
    from quonfig.logging import _level_name_for_structlog

    assert _level_name_for_structlog("not-a-level", {}) is None


# --- processor behaviour ---------------------------------------------------


def test_processor_gates_below_configured_level(tmp_path):
    dd = _fixed_level_datadir(tmp_path, "info")
    client = Quonfig(
        datadir=dd, environment="Production", logger_key="log-level.my-app"
    ).init()
    p = QuonfigLoggerProcessor(client)
    logger = _NamedLogger("my.app")

    # DEBUG dropped
    with pytest.raises(DropEvent):
        p(logger, "debug", {"event": "dbg"})

    # INFO passes
    out = p(logger, "info", {"event": "inf"})
    assert out == {"event": "inf"}

    # ERROR passes
    out = p(logger, "error", {"event": "err"})
    assert out == {"event": "err"}


def test_processor_uses_structlog_add_log_level_output(tmp_path):
    """structlog.stdlib.add_log_level sets event_dict['level'] — we honour it."""
    dd = _fixed_level_datadir(tmp_path, "warn")
    client = Quonfig(
        datadir=dd, environment="Production", logger_key="log-level.my-app"
    ).init()
    p = QuonfigLoggerProcessor(client)
    logger = _NamedLogger("my.app")

    # add_log_level would stamp "level": "info"; that's below warn → drop
    with pytest.raises(DropEvent):
        p(logger, "msg", {"level": "info", "event": "x"})

    # "warning" at WARN → emit
    out = p(logger, "msg", {"level": "warning", "event": "x"})
    assert out["event"] == "x"


def test_processor_passes_through_when_client_is_none():
    p = QuonfigLoggerProcessor(None)
    logger = _NamedLogger("anything")
    assert p(logger, "debug", {"event": "x"}) == {"event": "x"}


def test_processor_per_logger_rules_use_logger_name(tmp_path):
    dd = _per_logger_datadir(tmp_path)
    client = Quonfig(
        datadir=dd,
        environment="Production",
        logger_key="log-level.test-app",
    ).init()
    p = QuonfigLoggerProcessor(client)

    foo = _NamedLogger("foo.bar")
    noisy = _NamedLogger("noisy.thing")
    other = _NamedLogger("other.thing")

    # foo.bar → debug rule → debug emits
    assert p(foo, "debug", {"event": "d"}) == {"event": "d"}
    # noisy.thing → error rule → INFO dropped
    with pytest.raises(DropEvent):
        p(noisy, "info", {"event": "i"})
    # other → info default → DEBUG dropped
    with pytest.raises(DropEvent):
        p(other, "debug", {"event": "d"})


def test_processor_falls_back_to_event_dict_logger(tmp_path):
    """When the logger object has no .name, use event_dict['logger']."""
    dd = _per_logger_datadir(tmp_path)
    client = Quonfig(
        datadir=dd,
        environment="Production",
        logger_key="log-level.test-app",
    ).init()
    p = QuonfigLoggerProcessor(client)

    class AnonLogger:
        pass

    anon = AnonLogger()
    # foo.bar via event_dict → debug rule → debug emits
    out = p(anon, "debug", {"event": "d", "logger": "foo.bar"})
    assert out["event"] == "d"


def test_processor_logger_path_passes_through_unnormalized(tmp_path):
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
    client = Quonfig(
        datadir=dd, environment="Production", logger_key="log-level.native"
    ).init()
    p = QuonfigLoggerProcessor(client)

    # Exact unnormalized path → debug rule → info emits
    out = p(_NamedLogger("MyApp::Services::Auth"), "info", {"event": "x"})
    assert out["event"] == "x"

    # Normalized path → warn default → info dropped
    with pytest.raises(DropEvent):
        p(_NamedLogger("my_app.services.auth"), "info", {"event": "x"})


def test_processor_no_level_noop(tmp_path):
    """With an unknown method_name and no level info, we don't filter."""
    dd = _fixed_level_datadir(tmp_path, "error")
    client = Quonfig(
        datadir=dd, environment="Production", logger_key="log-level.my-app"
    ).init()
    p = QuonfigLoggerProcessor(client)
    out = p(_NamedLogger("x"), "not-a-level", {"event": "keep"})
    assert out == {"event": "keep"}


def test_processor_no_logger_name_noop(tmp_path):
    """No logger name + no event_dict['logger'] → pass-through."""
    dd = _fixed_level_datadir(tmp_path, "error")
    client = Quonfig(
        datadir=dd, environment="Production", logger_key="log-level.my-app"
    ).init()
    p = QuonfigLoggerProcessor(client)

    class AnonLogger:
        pass

    assert p(AnonLogger(), "debug", {"event": "x"}) == {"event": "x"}


def test_processor_allows_event_when_evaluation_raises(monkeypatch, tmp_path):
    dd = _fixed_level_datadir(tmp_path, "error")
    client = Quonfig(
        datadir=dd, environment="Production", logger_key="log-level.my-app"
    ).init()

    def boom(*args, **kwargs):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(client, "should_log", boom)

    p = QuonfigLoggerProcessor(client)
    # Would normally be dropped (debug < error), but exception → pass through.
    assert p(_NamedLogger("x"), "debug", {"event": "m"}) == {"event": "m"}


def test_processor_integrates_with_structlog_pipeline(tmp_path, capsys):
    """End-to-end: wire into a real structlog pipeline with add_log_level."""
    dd = _fixed_level_datadir(tmp_path, "info")
    client = Quonfig(
        datadir=dd, environment="Production", logger_key="log-level.my-app"
    ).init()

    def _add_logger_name(logger, method_name, event_dict):
        # PrintLogger doesn't expose a name; inject one so QuonfigLoggerProcessor
        # can look up per-logger rules via event_dict["logger"].
        event_dict.setdefault("logger", "my.app")
        return event_dict

    structlog.configure(
        processors=[
            _add_logger_name,
            structlog.stdlib.add_log_level,
            QuonfigLoggerProcessor(client),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10),  # DEBUG
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    try:
        log = structlog.get_logger()
        log.debug("dropped")
        log.info("kept")
        log.error("kept-error")
        out = capsys.readouterr().out
        assert "dropped" not in out
        assert "kept" in out
        assert "kept-error" in out
    finally:
        structlog.reset_defaults()


def test_processor_import_error_when_structlog_missing(monkeypatch):
    """If structlog isn't installed, instantiation raises ImportError."""
    import quonfig.logging as qlogging

    monkeypatch.setattr(qlogging, "_STRUCTLOG_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        QuonfigLoggerProcessor(None)
    assert "structlog" in str(exc_info.value).lower()
