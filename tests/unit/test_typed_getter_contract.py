"""The typed-getter contract (qfg-zvyy, 3.0.0) beyond what the
integration-test-data matrix reaches: once-per-key logging,
``is_feature_enabled`` never raising, the int <-> double edges, defaults
returned unchanged, weighted variants, and plain/details agreement.

The matrix itself (every getter x every stored type, missing ENV_VAR,
decryption failure, malformed values) runs through the public getters in
``tests/integration/test_get.py``, ``test_get_or_raise.py`` and
``test_enabled.py``.
"""

from __future__ import annotations

import logging
import math
from typing import Any, List

import pytest

from quonfig import Quonfig
from quonfig.evaluator import Evaluator
from quonfig.exceptions import (
    QuonfigDecryptionError,
    QuonfigEnvVarCoerceError,
    QuonfigEnvVarNotSetError,
    QuonfigInitError,
    QuonfigKeyNotFoundError,
    QuonfigTypeMismatchError,
)
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

MAX_SAFE_INT = 2**53 - 1
UNSET_ENV = "QUONFIG_TYPED_CONTRACT_UNSET_VAR"


def _config(key: str, value_type: str, value: Value, criteria: Any = None) -> ConfigResponse:
    return ConfigResponse(
        id=key,
        key=key,
        type="config",
        value_type=value_type,
        send_to_client_sdk=False,
        default=RuleSet(
            rules=[
                Rule(
                    criteria=criteria or [Criterion(operator="ALWAYS_TRUE")],
                    value=value,
                )
            ]
        ),
        environment=Environment(id="Production", rules=[]),
    )


def _plain(key: str, value_type: str, raw: Any) -> ConfigResponse:
    return _config(key, value_type, Value(type=value_type, value=raw))


def _client(configs: List[ConfigResponse], on_no_default: str = "ignore") -> Quonfig:
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment="Production",
        on_init_failure="continue",
        on_no_default=on_no_default,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    c._store.update(
        ConfigEnvelope(configs=configs, meta=Meta(version="test", environment="Production"))
    )
    c._evaluator = Evaluator(c._store, "Production")
    c._started = True
    return c


def _read_failure_warnings(caplog: pytest.LogCaptureFixture, key: str) -> List[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and f"read of '{key}'" in r.getMessage()
    ]


@pytest.fixture(autouse=True)
def _no_unset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(UNSET_ENV, raising=False)


# --------------------------------------------------------------------------
# int <-> double: safe conversions only
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, expected",
    [
        (3.0, 3),
        (-3.0, -3),
        (0.0, 0),
        (float(MAX_SAFE_INT), MAX_SAFE_INT),
        (-float(MAX_SAFE_INT), -MAX_SAFE_INT),
    ],
)
def test_int_getter_reads_whole_safe_doubles(stored: float, expected: int) -> None:
    c = _client([_plain("d", "double", stored)])
    got = c.get_int("d")
    assert got == expected
    assert isinstance(got, int) and not isinstance(got, bool)
    assert c.get_int_details("d").value == expected


@pytest.mark.parametrize(
    "stored",
    [3.9, -0.5, 1e300, float(2**53), -float(2**53), math.nan, math.inf, -math.inf],
)
def test_int_getter_never_truncates_or_overflows(stored: float) -> None:
    c = _client([_plain("d", "double", stored)])
    assert c.get_int("d", default=7) == 7
    assert c.get_int("d") is None
    details = c.get_int_details("d")
    assert (details.reason, details.error_code, details.value) == ("ERROR", "TYPE_MISMATCH", None)
    with pytest.raises(QuonfigTypeMismatchError):
        _client([_plain("d", "double", stored)], on_no_default="error").get_int("d")


def test_float_getter_reads_ints() -> None:
    c = _client([_plain("i", "int", 30)])
    got = c.get_float("i")
    assert got == 30.0 and isinstance(got, float)
    assert c.get_float_details("i").value == 30.0


def test_float_getter_on_an_int_too_large_for_a_float_is_a_mismatch() -> None:
    huge = 10**400
    c = _client([_plain("i", "int", huge)])
    assert c.get_float("i", default=1.5) == 1.5
    assert c.get_float("i") is None
    details = c.get_float_details("i")
    assert (details.reason, details.error_code, details.value) == ("ERROR", "TYPE_MISMATCH", None)
    with pytest.raises(QuonfigTypeMismatchError):
        _client([_plain("i", "int", huge)], on_no_default="error").get_float("i")


def test_type_mismatch_error_is_exported_from_the_package_root() -> None:
    import quonfig

    assert quonfig.QuonfigTypeMismatchError is QuonfigTypeMismatchError
    assert "QuonfigTypeMismatchError" in quonfig.__all__
    assert issubclass(quonfig.QuonfigTypeMismatchError, quonfig.QuonfigError)


def test_bool_is_never_a_number() -> None:
    c = _client([_plain("b", "bool", True)])
    assert c.get_int("b", default=7) == 7
    assert c.get_float("b", default=1.5) == 1.5


def test_int_is_never_a_bool() -> None:
    c = _client([_plain("i", "int", 1)])
    assert c.get_bool("i", default=False) is False
    assert c.is_feature_enabled("i") is False


# --------------------------------------------------------------------------
# no string coercion; plain and details getters agree
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "getter, details, stored_type, stored",
    [
        ("get_bool", "get_bool_details", "string", "false"),
        ("get_bool", "get_bool_details", "string", "true"),
        ("get_int", "get_int_details", "string", "3"),
        ("get_float", "get_float_details", "string", "3.5"),
        ("get_string", "get_string_details", "int", 3),
        ("get_string", "get_string_details", "bool", False),
        ("get_string_list", "get_string_list_details", "string", "fast"),
        ("get_json", "get_json_details", "string", "{}"),
    ],
)
def test_plain_and_details_agree_on_a_mismatch(
    getter: str, details: str, stored_type: str, stored: Any
) -> None:
    c = _client([_plain("k", stored_type, stored)])
    sentinel = object()
    assert getattr(c, getter)("k", default=sentinel) is sentinel
    assert getattr(c, getter)("k") is None
    d = getattr(c, details)("k")
    assert (d.value, d.reason, d.error_code) == (None, "ERROR", "TYPE_MISMATCH")


def test_log_level_reads_as_a_string() -> None:
    c = _client([_plain("lvl", "log_level", "debug")])
    assert c.get_string("lvl") == "DEBUG"


def test_default_is_returned_unchanged() -> None:
    """2.x ran the default through bool()/int()/str(); 3.0 hands it back."""
    c = _client([])
    assert c.get_bool("missing", default="false") == "false"
    assert c.get_int("missing", default="5") == "5"
    assert c.get_string_list("missing", default="x") == "x"
    c2 = _client([_plain("s", "string", "fast")])
    assert c2.get_bool("s", default="false") == "false"


def test_weighted_variant_type_is_checked() -> None:
    weighted = Value(
        type="weighted_values",
        value={
            "hashByPropertyName": "user.key",
            "weightedValues": [{"weight": 1, "value": {"type": "string", "value": "on"}}],
        },
    )
    c = _client([_config("w", "bool", weighted)])
    ctx = {"user": {"key": "u1"}}
    assert c.get_bool("w", default=True, contexts=ctx) is True
    assert c.get_string("w", contexts=ctx) == "on"
    assert c.get_bool_details("w", contexts=ctx).error_code == "TYPE_MISMATCH"


# --------------------------------------------------------------------------
# resolution errors: default, None, or raise
# --------------------------------------------------------------------------


def _env_config() -> ConfigResponse:
    return _config(
        "env", "string", Value(type="provided", value={"source": "ENV_VAR", "lookup": UNSET_ENV})
    )


def _encrypted_config() -> ConfigResponse:
    return _config(
        "secret",
        "string",
        Value(type="string", value="ciphertext", confidential=True, decrypt_with="no.such.key"),
    )


@pytest.mark.parametrize(
    "config, key, error",
    [
        (_env_config, "env", QuonfigEnvVarNotSetError),
        (_encrypted_config, "secret", QuonfigDecryptionError),
    ],
)
def test_resolution_error_uses_default_none_or_raises(config: Any, key: str, error: type) -> None:
    c = _client([config()])
    assert c.get_string(key, default="d") == "d"
    assert c.get(key, default="d") == "d"
    assert c.get_string(key) is None
    assert c.is_feature_enabled(key) is False
    strict = _client([config()], on_no_default="error")
    assert strict.get_string(key, default="d") == "d"
    with pytest.raises(error):
        strict.get_string(key)
    with pytest.raises(error):
        strict.get(key)


def test_malformed_stored_int_raises_coerce_error_without_default() -> None:
    c = _client([_plain("i", "int", "twelve")], on_no_default="error")
    assert c.get_int("i", default=4) == 4
    with pytest.raises(QuonfigEnvVarCoerceError) as excinfo:
        c.get_int("i")
    assert "twelve" not in str(excinfo.value)
    # Still a QuonfigKeyNotFoundError subclass, so 2.x handlers keep working.
    assert isinstance(excinfo.value, QuonfigKeyNotFoundError)
    assert c.get_int_details("i").error_code == "PARSE_ERROR"


# --------------------------------------------------------------------------
# once-per-key logging
# --------------------------------------------------------------------------


def test_failed_read_logs_once_per_key(caplog: pytest.LogCaptureFixture) -> None:
    c = _client([_plain("a", "string", "secret-ish"), _plain("b", "string", "x")])
    with caplog.at_level(logging.WARNING, logger="quonfig"):
        for _ in range(5):
            c.get_bool("a", default=False)
            c.get_int("a", default=0)
            c.get_bool_details("a")
            c.is_feature_enabled("a")
        c.get_bool("b", default=False)
    a = _read_failure_warnings(caplog, "a")
    assert len(a) == 1, a
    assert "secret-ish" not in a[0]
    assert len(_read_failure_warnings(caplog, "b")) == 1


def test_log_once_is_per_client(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="quonfig"):
        for _ in range(2):
            _client([_plain("a", "string", "x")]).get_bool("a", default=False)
    assert len(_read_failure_warnings(caplog, "a")) == 2


def test_missing_key_is_not_a_read_failure(caplog: pytest.LogCaptureFixture) -> None:
    c = _client([])
    with caplog.at_level(logging.WARNING, logger="quonfig"):
        assert c.get_string("missing", default="d") == "d"
        assert c.get_string("missing") is None
    assert _read_failure_warnings(caplog, "missing") == []


# --------------------------------------------------------------------------
# is_feature_enabled never raises
# --------------------------------------------------------------------------


def test_is_feature_enabled_true_only_for_bool_true() -> None:
    c = _client([_plain("on", "bool", True), _plain("off", "bool", False)])
    assert c.is_feature_enabled("on") is True
    assert c.is_feature_enabled("off") is False


def test_is_feature_enabled_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    no_match = _config(
        "nomatch",
        "bool",
        Value(type="bool", value=True),
        criteria=[
            Criterion(
                operator="PROP_IS_ONE_OF",
                property_name="user.plan",
                value_to_match=Value(type="string_list", value=["pro"]),
            )
        ],
    )
    configs = [
        no_match,
        _plain("str", "string", "true"),
        _plain("json", "json", {"on": True}),
        _env_config(),
        _encrypted_config(),
    ]
    # on_no_default="error" makes the plain getters raise; is_feature_enabled must not.
    c = _client(configs, on_no_default="error")
    keys = ["missing", "nomatch", "str", "json", "env", "secret"]
    with caplog.at_level(logging.WARNING, logger="quonfig"):
        for _ in range(3):
            for key in keys:
                assert c.is_feature_enabled(key) is False, key
    for key in keys:
        assert len(_read_failure_warnings(caplog, key)) == 1, key


def test_is_feature_enabled_swallows_a_sticky_init_error(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client([_plain("on", "bool", True)])

    def _boom() -> None:
        raise QuonfigInitError("fork rebuild failed", reason="unavailable")

    monkeypatch.setattr(c, "_wait_initialized", _boom)
    assert c.is_feature_enabled("on") is False
    with pytest.raises(QuonfigInitError):
        c.get_bool("on")
