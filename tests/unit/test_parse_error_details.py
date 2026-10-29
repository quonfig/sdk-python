"""PARSE_ERROR in the ``*_details`` API and the stored-form
``evaluate_details`` read (qfg-wg1n, qfg-177f).

A value that is present but cannot be parsed as its config type -- a
malformed ENV_VAR value, a malformed stored int / double / duration --
reports ``error_code="PARSE_ERROR"``. A wrong type stays ``TYPE_MISMATCH``.
An unset ENV_VAR, a decryption failure and a stringified JSON value stay
``GENERAL``.

``evaluate_details`` returns the value in its stored form with no type check:
a duration is its ISO 8601 text exactly as stored. ENV_VAR provision and
decryption still apply.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from quonfig import Quonfig
from quonfig.evaluator import Evaluator
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

ENV = "QFG_WG1N_PARSE_ERROR_VALUE"


def _config(key: str, value_type: str, value: Value) -> ConfigResponse:
    return ConfigResponse(
        id=f"id-{key}",
        key=key,
        type="config",
        value_type=value_type,
        send_to_client_sdk=False,
        default=RuleSet(rules=[Rule(criteria=[Criterion(operator="ALWAYS_TRUE")], value=value)]),
        environment=Environment(id="Production", rules=[]),
    )


def _plain(key: str, value_type: str, raw: Any) -> ConfigResponse:
    return _config(key, value_type, Value(type=value_type, value=raw))


def _env(key: str, value_type: str) -> ConfigResponse:
    return _config(
        key, value_type, Value(type="provided", value={"source": "ENV_VAR", "lookup": ENV})
    )


def _weighted(key: str, value_type: str, variant: dict) -> ConfigResponse:
    return _config(
        key,
        value_type,
        Value(
            type="weighted_values",
            value={"weightedValues": [{"weight": 100, "value": variant}]},
        ),
    )


def _client(configs: List[ConfigResponse]) -> Quonfig:
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment="Production",
        on_init_failure="continue",
        on_no_default="ignore",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    c._store.update(
        ConfigEnvelope(configs=configs, meta=Meta(version="test", environment="Production"))
    )
    c._evaluator = Evaluator(c._store, "Production")
    c._started = True
    return c


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)


def _assert_error(d: Any, code: str, secret: str = "") -> None:
    assert d.reason == "ERROR"
    assert d.value is None
    assert d.error_code == code
    if secret:
        assert secret not in (d.error_message or "")


# --------------------------------------------------------------------------
# typed *_details: PARSE_ERROR for malformed values
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value_type, getter, raw",
    [
        ("int", "get_int_details", "banana"),
        ("double", "get_float_details", "banana"),
        ("bool", "get_bool_details", "banana"),
        ("duration", "get_json_details", "banana"),
    ],
)
def test_malformed_env_var_is_parse_error(
    monkeypatch: pytest.MonkeyPatch, value_type: str, getter: str, raw: str
) -> None:
    monkeypatch.setenv(ENV, raw)
    c = _client([_env("p.key", value_type)])
    _assert_error(getattr(c, getter)("p.key"), "PARSE_ERROR", raw)


def test_malformed_env_var_in_weighted_variant_is_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "banana")
    c = _client(
        [
            _weighted(
                "w.key", "int", {"type": "provided", "value": {"source": "ENV_VAR", "lookup": ENV}}
            )
        ]
    )
    _assert_error(c.get_int_details("w.key"), "PARSE_ERROR", "banana")


@pytest.mark.parametrize(
    "value_type, getter, raw",
    [
        ("int", "get_int_details", "twelve"),
        ("int", "get_float_details", "twelve"),
        ("double", "get_float_details", "twelve"),
        ("double", "get_int_details", "twelve"),
    ],
)
def test_malformed_stored_number_is_parse_error(value_type: str, getter: str, raw: str) -> None:
    c = _client([_plain("n.key", value_type, raw)])
    _assert_error(getattr(c, getter)("n.key"), "PARSE_ERROR", raw)


def test_malformed_stored_duration_is_parse_error() -> None:
    c = _client([_plain("d.key", "duration", "30s")])
    _assert_error(c.get_json_details("d.key"), "PARSE_ERROR", "30s")


def test_wrong_type_stays_type_mismatch() -> None:
    c = _client([_plain("s.key", "string", "hello")])
    _assert_error(c.get_int_details("s.key"), "TYPE_MISMATCH")


def test_unset_env_var_stays_general() -> None:
    c = _client([_env("p.key", "int")])
    _assert_error(c.get_int_details("p.key"), "GENERAL")
    _assert_error(c.evaluate_details("p.key"), "GENERAL")


def test_decryption_failure_stays_general() -> None:
    cfg = _config(
        "secret.key",
        "string",
        Value(type="string", value="not-ciphertext", confidential=True, decrypt_with="missing.key"),
    )
    c = _client([cfg])
    _assert_error(c.get_string_details("secret.key"), "GENERAL")
    _assert_error(c.evaluate_details("secret.key"), "GENERAL")


def test_stringified_json_stays_general() -> None:
    c = _client([_plain("j.key", "json", '{"a": 1}')])
    _assert_error(c.get_json_details("j.key"), "GENERAL")
    _assert_error(c.evaluate_details("j.key"), "GENERAL")


# --------------------------------------------------------------------------
# evaluate_details: the stored-form (untyped) read
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value_type, raw",
    [
        ("string", "hello"),
        ("int", 42),
        ("double", 1.5),
        ("bool", False),
        ("string_list", ["a", "b"]),
        ("json", {"a": [1, 2]}),
        ("log_level", "INFO"),
        ("duration", "PT90S"),
        ("duration", "P1DT6H2M1.5S"),
    ],
)
def test_evaluate_details_returns_stored_form(value_type: str, raw: Any) -> None:
    c = _client([_plain("k", value_type, raw)])
    d = c.evaluate_details("k")
    assert d.reason == "STATIC"
    assert d.error_code is None
    assert d.value == raw
    assert type(d.value) is type(raw)
    assert d.value_type == value_type
    assert d.variant == "static"
    assert d.flag_metadata is not None and d.flag_metadata.get("config_type") == "config"


def test_evaluate_details_duration_differs_from_typed_read() -> None:
    c = _client([_plain("d", "duration", "PT90S")])
    assert c.get_duration("d") == 90.0
    assert c.evaluate_details("d").value == "PT90S"


def test_evaluate_details_does_not_share_json_with_store() -> None:
    c = _client([_plain("j", "json", {"a": [1]})])
    c.evaluate_details("j").value["a"].append(2)
    assert c.evaluate_details("j").value == {"a": [1]}


def test_evaluate_details_env_var_is_coerced_to_config_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "42")
    c = _client([_env("p.key", "int")])
    d = c.evaluate_details("p.key")
    assert d.value == 42 and d.value_type == "int"


def test_evaluate_details_env_var_duration_is_stored_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "PT5M")
    c = _client([_env("p.key", "duration")])
    d = c.evaluate_details("p.key")
    assert d.value == "PT5M" and d.value_type == "duration"
    # The typed read still parses it.
    assert c.get_duration("p.key") == 300.0


def test_evaluate_details_weighted_duration_is_stored_text() -> None:
    c = _client([_weighted("w", "duration", {"type": "duration", "value": "PT2S"})])
    d = c.evaluate_details("w")
    assert d.value == "PT2S"
    assert d.value_type == "duration"
    assert d.reason == "SPLIT"


def test_evaluate_details_malformed_env_var_is_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV, "banana")
    c = _client([_env("p.key", "int"), _env("d.key", "duration")])
    _assert_error(c.evaluate_details("p.key"), "PARSE_ERROR", "banana")
    _assert_error(c.evaluate_details("d.key"), "PARSE_ERROR", "banana")


def test_evaluate_details_malformed_stored_duration_is_parse_error() -> None:
    c = _client([_plain("d", "duration", "30s")])
    _assert_error(c.evaluate_details("d"), "PARSE_ERROR", "30s")


def test_evaluate_details_missing_key_is_flag_not_found() -> None:
    c = _client([])
    d = c.evaluate_details("nope")
    _assert_error(d, "FLAG_NOT_FOUND")
    assert d.value_type is None


def test_evaluate_details_on_bound_client() -> None:
    c = _client([_plain("d", "duration", "PT1M")])
    d = c.with_context({"user": {"key": "u1"}}).evaluate_details("d")
    assert d.value == "PT1M" and d.value_type == "duration"


def test_typed_details_leave_value_type_unset() -> None:
    c = _client([_plain("i", "int", 3)])
    assert c.get_int_details("i").value_type is None
