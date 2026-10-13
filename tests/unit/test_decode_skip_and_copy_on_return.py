"""WS8 rules (qfg-goi1.10.5), matching sdk-go qfg-9dxb.6:

1. One config that fails to decode is skipped with a WARN naming its key;
   the rest of the envelope installs (2f6b98b). If every entry fails, the
   envelope is still rejected.
2. A malformed weighted-value weight fails that config's decode, instead of
   raising TypeError out of the getters at evaluation time.
3. get_json returns a copy, so a caller mutation cannot change the stored
   config (543ee9c).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

import pytest

from quonfig import Quonfig
from quonfig.types import ConfigEnvelope, InvalidEnvelopeError

_META = {"version": "v1", "environment": "production", "generation": 1}


def _string_config(key: str, value: str) -> Dict[str, Any]:
    return {
        "id": key,
        "key": key,
        "type": "config",
        "valueType": "string",
        "default": {
            "rules": [
                {
                    "criteria": [{"operator": "ALWAYS_TRUE"}],
                    "value": {"type": "string", "value": value},
                }
            ]
        },
    }


def _weighted_config(key: str, weight: Any) -> Dict[str, Any]:
    return {
        "id": key,
        "key": key,
        "type": "feature_flag",
        "valueType": "string",
        "default": {
            "rules": [
                {
                    "criteria": [{"operator": "ALWAYS_TRUE"}],
                    "value": {
                        "type": "weighted_values",
                        "value": {
                            "hashByPropertyName": "user.key",
                            "weightedValues": [
                                {"weight": weight, "value": {"type": "string", "value": "a"}},
                                {"weight": 1, "value": {"type": "string", "value": "b"}},
                            ],
                        },
                    },
                }
            ]
        },
    }


def _bad_config(key: str) -> Dict[str, Any]:
    # A criterion with no operator cannot be decoded.
    cfg = _string_config(key, "x")
    cfg["default"]["rules"][0]["criteria"] = [{"propertyName": "user.key"}]
    return cfg


def test_one_bad_config_is_skipped_and_the_rest_installs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {"configs": [_bad_config("bad.one"), _string_config("good", "ok")], "meta": _META}
    with caplog.at_level(logging.WARNING):
        env = ConfigEnvelope.from_wire(payload)
    assert [c.key for c in env.configs] == ["good"]
    assert any("bad.one" in r.getMessage() for r in caplog.records)


def test_every_config_bad_still_rejects_the_envelope() -> None:
    payload = {"configs": [_bad_config("bad.one"), _bad_config("bad.two")], "meta": _META}
    with pytest.raises(InvalidEnvelopeError):
        ConfigEnvelope.from_wire(payload)


@pytest.mark.parametrize("weight", ["abc", None, 1.5, True, {"n": 1}])
def test_malformed_weight_skips_that_config(weight: Any, caplog: pytest.LogCaptureFixture) -> None:
    payload = {
        "configs": [_weighted_config("rollout", weight), _string_config("good", "ok")],
        "meta": _META,
    }
    with caplog.at_level(logging.WARNING):
        env = ConfigEnvelope.from_wire(payload)
    assert [c.key for c in env.configs] == ["good"]
    assert any("rollout" in r.getMessage() for r in caplog.records)


def test_malformed_weight_in_datadir_is_skipped_not_a_getter_typeerror(tmp_path: Path) -> None:
    (tmp_path / "feature-flags").mkdir()
    (tmp_path / "feature-flags" / "rollout.json").write_text(
        json.dumps(_weighted_config("rollout", "abc"))
    )
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "good.json").write_text(json.dumps(_string_config("good", "ok")))
    client = Quonfig(datadir=str(tmp_path), environment="production", enable_sse=False)
    client.init()
    try:
        assert client.get_string("rollout", default="fallback") == "fallback"
        assert client.get_string("good") == "ok"
    finally:
        client.close()


def test_well_formed_weights_still_decode() -> None:
    payload = {"configs": [_weighted_config("rollout", 3)], "meta": _META}
    env = ConfigEnvelope.from_wire(payload)
    assert [c.key for c in env.configs] == ["rollout"]


def test_get_json_returns_a_copy(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    cfg = {
        "id": "j",
        "key": "settings",
        "type": "config",
        "valueType": "json",
        "default": {
            "rules": [
                {
                    "criteria": [{"operator": "ALWAYS_TRUE"}],
                    "value": {"type": "json", "value": {"limits": {"max": 5}, "tags": ["a"]}},
                }
            ]
        },
    }
    (tmp_path / "configs" / "settings.json").write_text(json.dumps(cfg))
    client = Quonfig(datadir=str(tmp_path), environment="production", enable_sse=False)
    client.init()
    try:
        first = client.get_json("settings")
        first["limits"]["max"] = 999
        first["tags"].append("mutated")
        assert client.get_json("settings") == {"limits": {"max": 5}, "tags": ["a"]}
        assert client.get("settings") == {"limits": {"max": 5}, "tags": ["a"]}
    finally:
        client.close()
