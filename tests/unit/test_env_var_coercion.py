"""ENV_VAR-provided values are coerced to the config's valueType (qfg-2agi.18).

Contract (qfg-2agi decision 3): a well-formed env string is coerced to the
config's valueType; a malformed one makes the typed getter return the
caller's default and log one warning per key; the no-default form returns
None; with on_no_default="error" (get_or_raise) it raises
QuonfigEnvVarCoerceError (unable_to_coerce_env_var). Duration grammar is the
decided one: fractions on S only.
"""

from __future__ import annotations

import logging

import pytest

from quonfig import Quonfig
from quonfig.evaluator import Evaluator
from quonfig.exceptions import QuonfigEnvVarCoerceError, QuonfigKeyNotFoundError
from quonfig.types import ConfigEnvelope, ConfigResponse, Meta

ENV = "QFG_2AGI_18_VALUE"


def _config(key: str, value_type: str) -> ConfigResponse:
    return ConfigResponse.from_dict(
        {
            "id": f"id-{key}",
            "key": key,
            "type": "config",
            "valueType": value_type,
            "sendToClientSdk": False,
            "environments": [],
            "default": {
                "rules": [
                    {
                        "criteria": [{"operator": "ALWAYS_TRUE"}],
                        "value": {
                            "type": "provided",
                            "value": {"source": "ENV_VAR", "lookup": ENV},
                        },
                    }
                ]
            },
        }
    )


def _client(value_type: str, on_no_default: str = "warn") -> Quonfig:
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment="Production",
        on_init_failure="return_zero_value",
        on_no_default=on_no_default,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    c._store.update(
        ConfigEnvelope(
            configs=[_config("p.key", value_type)],
            meta=Meta(version="test", environment="Production"),
        )
    )
    c._evaluator = Evaluator(c._store, "Production")
    c._initialized.set()
    return c


@pytest.mark.parametrize(
    "env,expected",
    [("false", False), ("true", True), ("FALSE", False), ("True", True)],
)
def test_bool(monkeypatch, env, expected):
    monkeypatch.setenv(ENV, env)
    c = _client("bool")
    assert c.get_bool("p.key", default=not expected) is expected
    assert c.get("p.key") is expected
    assert c.is_feature_enabled("p.key") is expected


def test_int(monkeypatch):
    monkeypatch.setenv(ENV, "1234")
    c = _client("int")
    assert c.get("p.key") == 1234
    assert isinstance(c.get("p.key"), int)
    assert c.get_int("p.key", default=0) == 1234


def test_double(monkeypatch):
    monkeypatch.setenv(ENV, "1.5")
    c = _client("double")
    assert c.get("p.key") == 1.5
    assert isinstance(c.get("p.key"), float)
    assert c.get_float("p.key", default=0.0) == 1.5


def test_string_list(monkeypatch):
    monkeypatch.setenv(ENV, "a,b, c")
    c = _client("string_list")
    assert c.get_string_list("p.key", default=[]) == ["a", "b", "c"]
    assert c.get("p.key") == ["a", "b", "c"]


@pytest.mark.parametrize(
    "env,seconds",
    [
        ("PT5S", 5.0),
        ("PT1.5S", 1.5),
        ("PT2.01S", 2.01),
        ("PT1.005S", 1.005),
        ("PT0.0005S", 0.001),
        ("PT0.0004S", 0.0),
        ("PT30M", 1800.0),
        ("P1DT1H", 90000.0),
    ],
)
def test_duration_valid(monkeypatch, env, seconds):
    monkeypatch.setenv(ENV, env)
    c = _client("duration")
    assert c.get_duration("p.key", default=-1.0) == seconds


MALFORMED = [
    ("bool", "yes", "get_bool", False),
    ("bool", "banana", "get_bool", False),
    ("int", "1.5", "get_int", 7),
    ("int", "abc", "get_int", 7),
    ("double", "abc", "get_float", 7.5),
    ("double", "nan", "get_float", 7.5),
    ("double", "inf", "get_float", 7.5),
    ("duration", "30", "get_duration", 9.0),
    ("duration", "30s", "get_duration", 9.0),
    ("duration", "nan", "get_duration", 9.0),
    ("duration", "1e3", "get_duration", 9.0),
    ("duration", "-PT5S", "get_duration", 9.0),
    ("duration", "PT", "get_duration", 9.0),
    ("duration", "P1DT", "get_duration", 9.0),
    ("duration", "PT0.5H", "get_duration", 9.0),
    ("duration", "PT1.5M", "get_duration", 9.0),
    ("duration", "P", "get_duration", 9.0),
    ("duration", "PT0.1234567891S", "get_duration", 9.0),
    ("duration", "P36501D", "get_duration", 9.0),
]


@pytest.mark.parametrize("vtype,env,getter,default", MALFORMED)
def test_malformed_returns_default_and_logs_once(monkeypatch, caplog, vtype, env, getter, default):
    monkeypatch.setenv(ENV, env)
    c = _client(vtype)
    with caplog.at_level(logging.WARNING, logger="quonfig"):
        assert getattr(c, getter)("p.key", default=default) == default
        assert getattr(c, getter)("p.key", default=default) == default
    coerce_logs = [r for r in caplog.records if "could not be coerced" in r.getMessage()]
    assert len(coerce_logs) == 1
    assert env not in coerce_logs[0].getMessage()  # raw value never logged


@pytest.mark.parametrize("vtype,env,getter,default", MALFORMED)
def test_malformed_no_default_returns_none(monkeypatch, vtype, env, getter, default):
    monkeypatch.setenv(ENV, env)
    c = _client(vtype, on_no_default="ignore")
    assert getattr(c, getter)("p.key") is None


@pytest.mark.parametrize("vtype,env,getter,default", MALFORMED)
def test_malformed_get_or_raise_raises_coerce_error(monkeypatch, vtype, env, getter, default):
    monkeypatch.setenv(ENV, env)
    c = _client(vtype, on_no_default="error")
    with pytest.raises(QuonfigEnvVarCoerceError):
        getattr(c, getter)("p.key")
    # Back-compat: the generated ITD harness maps unable_to_coerce_env_var to
    # QuonfigKeyNotFoundError, so the new class must stay a subclass.
    with pytest.raises(QuonfigKeyNotFoundError):
        getattr(c, getter)("p.key")


def test_malformed_details_reports_error(monkeypatch):
    monkeypatch.setenv(ENV, "banana")
    c = _client("int")
    d = c.get_int_details("p.key")
    assert d.reason == "ERROR"
    assert d.value is None
    assert "banana" not in (d.error_message or "")
