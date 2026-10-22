"""Getters have an exception barrier (qfg-6vno.14).

1. A context namespace whose value is not a mapping (``{"user": None}``) is
   skipped when contexts merge, so plain getters return the default instead
   of raising TypeError (sdk-node context.ts tolerates it the same way).
2. A non-string ``propertyName`` fails that config's decode, so the config is
   skipped with a WARN instead of raising AttributeError out of every getter.
3. Telemetry recording in ``_get`` cannot break a getter, matching
   ``_evaluate_details``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

import pytest

from quonfig import Quonfig
from quonfig.context import merge_contexts
from quonfig.types import ConfigEnvelope

_META = {"version": "v1", "environment": "production", "generation": 1}


def _string_config(key: str, value: str, criteria: Any = None) -> Dict[str, Any]:
    return {
        "id": key,
        "key": key,
        "type": "config",
        "valueType": "string",
        "default": {
            "rules": [
                {
                    "criteria": criteria or [{"operator": "ALWAYS_TRUE"}],
                    "value": {"type": "string", "value": value},
                }
            ]
        },
    }


def _targeted_config(key: str, property_name: Any) -> Dict[str, Any]:
    return _string_config(
        key,
        "targeted",
        criteria=[
            {
                "operator": "PROP_IS_ONE_OF",
                "propertyName": property_name,
                "valueToMatch": {"type": "string_list", "value": ["a"]},
            }
        ],
    )


def _client(tmp_path: Path, *configs: Dict[str, Any]) -> Quonfig:
    (tmp_path / "configs").mkdir()
    for cfg in configs:
        (tmp_path / "configs" / f"{cfg['key']}.json").write_text(json.dumps(cfg))
    client = Quonfig(datadir=str(tmp_path), environment="production", enable_sse=False)
    client.init()
    return client


def test_merge_contexts_skips_non_mapping_namespace() -> None:
    merged = merge_contexts({"user": None, "team": {"key": "t1"}})  # type: ignore[dict-item]
    assert merged == {"team": {"key": "t1"}}


def test_none_namespace_getters_return_default(tmp_path: Path) -> None:
    client = _client(tmp_path, _string_config("good", "ok"), _targeted_config("t", "user.key"))
    ctx = {"user": None}
    try:
        assert client.get_string("missing", default="d", contexts=ctx) == "d"  # type: ignore[arg-type]
        assert client.get_bool("missing", default=True, contexts=ctx) is True  # type: ignore[arg-type]
        assert client.is_feature_enabled("missing", contexts=ctx) is False  # type: ignore[arg-type]
        assert client.get_string("good", contexts=ctx) == "ok"  # type: ignore[arg-type]
        assert client.get_string("t", default="d", contexts=ctx) == "d"  # type: ignore[arg-type]
    finally:
        client.close()


@pytest.mark.parametrize("prop", [7, 1.5, True, ["user.key"], {"a": 1}])
def test_non_string_property_name_skips_that_config(
    prop: Any, caplog: pytest.LogCaptureFixture
) -> None:
    payload = {
        "configs": [_targeted_config("bad", prop), _string_config("good", "ok")],
        "meta": _META,
    }
    with caplog.at_level(logging.WARNING):
        env = ConfigEnvelope.from_wire(payload)
    assert [c.key for c in env.configs] == ["good"]
    assert any("bad" in r.getMessage() for r in caplog.records)


def test_non_string_property_name_in_datadir_returns_default(tmp_path: Path) -> None:
    client = _client(tmp_path, _targeted_config("bad", 7), _string_config("good", "ok"))
    try:
        assert client.get_string("bad", default="d", contexts={"user": {"key": "a"}}) == "d"
        assert client.get_string("good") == "ok"
    finally:
        client.close()


class _RaisingTelemetry:
    def record_evaluation(self, result: Any) -> None:
        raise RuntimeError("boom")

    def record_context(self, contexts: Any) -> None:
        raise RuntimeError("boom")


class _BadStr:
    def __str__(self) -> str:
        raise RuntimeError("bad __str__")

    def __hash__(self) -> int:
        return 1


def test_raising_telemetry_does_not_break_get(tmp_path: Path) -> None:
    client = _client(tmp_path, _string_config("good", "ok"))
    try:
        client._telemetry = _RaisingTelemetry()  # type: ignore[assignment]
        assert client.get_string("good", contexts={"user": {"key": "a"}}) == "ok"
        assert client.get("good") == "ok"
    finally:
        client._telemetry = None
        client.close()


def test_raising_context_key_does_not_break_get(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "good.json").write_text(json.dumps(_string_config("good", "ok")))
    client = Quonfig(
        sdk_key="test-key",
        datadir=str(tmp_path),
        environment="production",
        enable_sse=False,
        telemetry_url="http://127.0.0.1:1",
    )
    client.init()
    try:
        assert client._telemetry is not None  # the real reporter records the context
        ctx = {"user": {_BadStr(): "x", "key": "a"}}
        assert client.get_string("good", contexts=ctx) == "ok"  # type: ignore[dict-item]
    finally:
        client.close()
