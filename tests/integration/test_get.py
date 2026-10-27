# AUTO-GENERATED from integration-test-data/tests/eval/get.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

import os

import pytest

from quonfig import Quonfig

DATADIR = os.path.join(
    os.path.dirname(__file__),
    "../../../integration-test-data/data/integration-tests",
)


@pytest.fixture(scope="module")
def config_client():
    os.environ.setdefault(
        "PREFAB_INTEGRATION_TEST_ENCRYPTION_KEY",
        "c87ba22d8662282abe8a0e4651327b579cb64a454ab0f4c170b45b15f049a221",
    )
    os.environ.setdefault("IS_A_NUMBER", "1234")
    os.environ.setdefault("NOT_A_NUMBER", "not_a_number")
    os.environ.pop("MISSING_ENV_VAR", None)
    c = Quonfig(
        datadir=DATADIR,
        environment="Production",
        on_init_failure="return_zero_value",
    )
    c.init()
    return c


# get returns a found value for key
def test_get_returns_a_found_value_for_key(config_client) -> None:
    c = config_client
    result = c.get_string("my-test-key")
    assert result == "my-test-value"


# get returns nil if value not found
def test_get_returns_nil_if_value_not_found() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("my-missing-key")
    assert result is None


# get returns a default for a missing value if a default is given
def test_get_returns_a_default_for_a_missing_value_if_a_default_is_given(config_client) -> None:
    c = config_client
    result = c.get_string("my-missing-key", default="DEFAULT")
    assert result == "DEFAULT"


# get ignores a provided default if the key is found
def test_get_ignores_a_provided_default_if_the_key_is_found(config_client) -> None:
    c = config_client
    result = c.get_string("my-test-key", default="DEFAULT")
    assert result == "my-test-value"


# get can return a double
def test_get_can_return_a_double(config_client) -> None:
    c = config_client
    result = c.get_float("my-double-key")
    assert abs(result - 9.95) < 1e-9


# get can return a string list
def test_get_can_return_a_string_list(config_client) -> None:
    c = config_client
    result = c.get_string_list("my-string-list-key")
    assert result == ["a", "b", "c"]


# can return a value provided by an environment variable
def test_can_return_a_value_provided_by_an_environment_variable(config_client) -> None:
    c = config_client
    result = c.get_string("prefab.secrets.encryption.key")
    assert result == "c87ba22d8662282abe8a0e4651327b579cb64a454ab0f4c170b45b15f049a221"


# can return a value provided by an environment variable after type coercion
def test_can_return_a_value_provided_by_an_environment_variable_after_type_coercion(
    config_client,
) -> None:
    c = config_client
    result = c.get_int("provided.a.number")
    assert result == 1234


# can decrypt and return a secret value (with decryption key in in env var)
def test_can_decrypt_and_return_a_secret_value_with_decryption_key_in_in_env_var(
    config_client,
) -> None:
    c = config_client
    result = c.get_string("a.secret.config")
    assert result == "hello.world"


# decrypts a secret whose key config has an environment rule
def test_decrypts_a_secret_whose_key_config_has_an_environment_rule(config_client) -> None:
    c = config_client
    result = c.get_string("env.scoped.secret")
    assert result == "hello.world"


# duration 200 ms
def test_duration_200_ms(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.2S")
    assert result == 200 / 1000, f"Expected exactly 200ms (200 / 1000 s), got {result!r}s"


# duration 90S
def test_duration_90s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT90S")
    assert result == 90000 / 1000, f"Expected exactly 90000ms (90000 / 1000 s), got {result!r}s"


# duration 30M
def test_duration_30m(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT30M")
    assert result == 1800000 / 1000, (
        f"Expected exactly 1800000ms (1800000 / 1000 s), got {result!r}s"
    )


# duration test.duration.P1DT6H2M1.5S
def test_duration_test_duration_p1dt6h2m1_5s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P1DT6H2M1.5S")
    assert result == 108121500 / 1000, (
        f"Expected exactly 108121500ms (108121500 / 1000 s), got {result!r}s"
    )


# duration zero PT0S
def test_duration_zero_pt0s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0S")
    assert result == 0 / 1000, f"Expected exactly 0ms (0 / 1000 s), got {result!r}s"


# duration zero P0D
def test_duration_zero_p0d(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P0D")
    assert result == 0 / 1000, f"Expected exactly 0ms (0 / 1000 s), got {result!r}s"


# duration days only P2D
def test_duration_days_only_p2d(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P2D")
    assert result == 172800000 / 1000, (
        f"Expected exactly 172800000ms (172800000 / 1000 s), got {result!r}s"
    )


# duration hours only PT1H
def test_duration_hours_only_pt1h(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1H")
    assert result == 3600000 / 1000, (
        f"Expected exactly 3600000ms (3600000 / 1000 s), got {result!r}s"
    )


# duration minutes only PT1M
def test_duration_minutes_only_pt1m(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1M")
    assert result == 60000 / 1000, f"Expected exactly 60000ms (60000 / 1000 s), got {result!r}s"


# duration seconds only PT1S
def test_duration_seconds_only_pt1s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1S")
    assert result == 1000 / 1000, f"Expected exactly 1000ms (1000 / 1000 s), got {result!r}s"


# duration leading zero PT05S
def test_duration_leading_zero_pt05s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT05S")
    assert result == 5000 / 1000, f"Expected exactly 5000ms (5000 / 1000 s), got {result!r}s"


# duration hours and minutes PT1H30M
def test_duration_hours_and_minutes_pt1h30m(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1H30M")
    assert result == 5400000 / 1000, (
        f"Expected exactly 5400000ms (5400000 / 1000 s), got {result!r}s"
    )


# duration days and hours P1DT2H
def test_duration_days_and_hours_p1dt2h(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P1DT2H")
    assert result == 93600000 / 1000, (
        f"Expected exactly 93600000ms (93600000 / 1000 s), got {result!r}s"
    )


# duration one millisecond PT0.001S
def test_duration_one_millisecond_pt0_001s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.001S")
    assert result == 1 / 1000, f"Expected exactly 1ms (1 / 1000 s), got {result!r}s"


# duration magnitude ceiling P36500D
def test_duration_magnitude_ceiling_p36500d(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P36500D")
    assert result == 3153600000000 / 1000, (
        f"Expected exactly 3153600000000ms (3153600000000 / 1000 s), got {result!r}s"
    )


# duration rounding PT2.01S
def test_duration_rounding_pt2_01s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT2.01S")
    assert result == 2010 / 1000, f"Expected exactly 2010ms (2010 / 1000 s), got {result!r}s"


# duration rounding PT1.005S
def test_duration_rounding_pt1_005s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1.005S")
    assert result == 1005 / 1000, f"Expected exactly 1005ms (1005 / 1000 s), got {result!r}s"


# duration rounding half up PT0.0005S
def test_duration_rounding_half_up_pt0_0005s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.0005S")
    assert result == 1 / 1000, f"Expected exactly 1ms (1 / 1000 s), got {result!r}s"


# duration rounding down PT0.0004S
def test_duration_rounding_down_pt0_0004s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.0004S")
    assert result == 0 / 1000, f"Expected exactly 0ms (0 / 1000 s), got {result!r}s"


# json test
def test_json_test(config_client) -> None:
    c = config_client
    result = c.get_json("test.json")
    assert result == {"a": 1, "b": "c"}


# get returns a native json object (not a stringified payload)
def test_get_returns_a_native_json_object_not_a_stringified_payload(config_client) -> None:
    c = config_client
    result = c.get_json("test.json")
    assert result == {"a": 1, "b": "c"}


# list on left side test (1)
def test_list_on_left_side_test_1(config_client) -> None:
    c = config_client
    result = c.get_string(
        "left.hand.list.test", contexts={"user": {"name": "james", "aka": ["happy", "sleepy"]}}
    )
    assert result == "correct"


# list on left side test (2)
def test_list_on_left_side_test_2(config_client) -> None:
    c = config_client
    result = c.get_string(
        "left.hand.list.test", contexts={"user": {"name": "james", "aka": ["a", "b"]}}
    )
    assert result == "default"


# list on left side test opposite (1)
def test_list_on_left_side_test_opposite_1(config_client) -> None:
    c = config_client
    result = c.get_string(
        "left.hand.test.opposite", contexts={"user": {"name": "james", "aka": ["happy", "sleepy"]}}
    )
    assert result == "default"


# list on left side test (3)
def test_list_on_left_side_test_3(config_client) -> None:
    c = config_client
    result = c.get_string(
        "left.hand.test.opposite", contexts={"user": {"name": "james", "aka": ["a", "b"]}}
    )
    assert result == "correct"


# env-var-provided duration PT1.5S via get
def test_env_var_provided_duration_pt1_5s_via_get() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_PT1_5S"] = os.environ.get("QUONFIG_ITD_DURATION_PT1_5S")
    os.environ["QUONFIG_ITD_DURATION_PT1_5S"] = "PT1.5S"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_duration("provided.duration.PT1.5S")
        assert result == 1500 / 1000, f"Expected exactly 1500ms (1500 / 1000 s), got {result!r}s"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# stored malformed duration 30s returns the default
def test_stored_malformed_duration_30s_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.30s", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# stored malformed duration 30s with no default returns nil
def test_stored_malformed_duration_30s_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.30s")
    assert result is None


# stored malformed duration PT0.5H returns the default
def test_stored_malformed_duration_pt0_5h_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.PT0.5H", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# stored malformed duration PT0.5H with no default returns nil
def test_stored_malformed_duration_pt0_5h_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.PT0.5H")
    assert result is None


# stored malformed duration P1DT returns the default
def test_stored_malformed_duration_p1dt_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.P1DT", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# stored malformed duration P1DT with no default returns nil
def test_stored_malformed_duration_p1dt_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.P1DT")
    assert result is None


# stored malformed duration garbage returns the default
def test_stored_malformed_duration_garbage_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.garbage", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# stored malformed duration garbage with no default returns nil
def test_stored_malformed_duration_garbage_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.garbage")
    assert result is None


# stored malformed duration empty returns the default
def test_stored_malformed_duration_empty_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.empty", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# stored malformed duration empty with no default returns nil
def test_stored_malformed_duration_empty_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.empty")
    assert result is None


# env-var-provided malformed duration 30s returns the default
def test_env_var_provided_malformed_duration_30s_returns_the_default() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_30S"] = os.environ.get("QUONFIG_ITD_DURATION_30S")
    os.environ["QUONFIG_ITD_DURATION_30S"] = "30s"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_duration("provided.duration.malformed.30s", default=7)
        assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# env-var-provided malformed duration 30s with no default returns nil
def test_env_var_provided_malformed_duration_30s_with_no_default_returns_nil() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_30S"] = os.environ.get("QUONFIG_ITD_DURATION_30S")
    os.environ["QUONFIG_ITD_DURATION_30S"] = "30s"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
        c.init()
        result = c.get_duration("provided.duration.malformed.30s")
        assert result is None
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# env-var-provided malformed duration PT0.5H returns the default
def test_env_var_provided_malformed_duration_pt0_5h_returns_the_default() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_PT0_5H"] = os.environ.get("QUONFIG_ITD_DURATION_PT0_5H")
    os.environ["QUONFIG_ITD_DURATION_PT0_5H"] = "PT0.5H"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_duration("provided.duration.malformed.PT0.5H", default=7)
        assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# env-var-provided malformed duration PT0.5H with no default returns nil
def test_env_var_provided_malformed_duration_pt0_5h_with_no_default_returns_nil() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_PT0_5H"] = os.environ.get("QUONFIG_ITD_DURATION_PT0_5H")
    os.environ["QUONFIG_ITD_DURATION_PT0_5H"] = "PT0.5H"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
        c.init()
        result = c.get_duration("provided.duration.malformed.PT0.5H")
        assert result is None
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# env-var-provided malformed duration P1DT returns the default
def test_env_var_provided_malformed_duration_p1dt_returns_the_default() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_P1DT"] = os.environ.get("QUONFIG_ITD_DURATION_P1DT")
    os.environ["QUONFIG_ITD_DURATION_P1DT"] = "P1DT"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_duration("provided.duration.malformed.P1DT", default=7)
        assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# env-var-provided malformed duration P1DT with no default returns nil
def test_env_var_provided_malformed_duration_p1dt_with_no_default_returns_nil() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_P1DT"] = os.environ.get("QUONFIG_ITD_DURATION_P1DT")
    os.environ["QUONFIG_ITD_DURATION_P1DT"] = "P1DT"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
        c.init()
        result = c.get_duration("provided.duration.malformed.P1DT")
        assert result is None
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# env-var-provided malformed duration garbage returns the default
def test_env_var_provided_malformed_duration_garbage_returns_the_default() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_GARBAGE"] = os.environ.get("QUONFIG_ITD_DURATION_GARBAGE")
    os.environ["QUONFIG_ITD_DURATION_GARBAGE"] = "garbage"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_duration("provided.duration.malformed.garbage", default=7)
        assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# env-var-provided malformed duration garbage with no default returns nil
def test_env_var_provided_malformed_duration_garbage_with_no_default_returns_nil() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_DURATION_GARBAGE"] = os.environ.get("QUONFIG_ITD_DURATION_GARBAGE")
    os.environ["QUONFIG_ITD_DURATION_GARBAGE"] = "garbage"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
        c.init()
        result = c.get_duration("provided.duration.malformed.garbage")
        assert result is None
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# STRING getter on stored int returns the default
def test_string_getter_on_stored_int_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("getter.contract.int", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on stored int with no default returns nil
def test_string_getter_on_stored_int_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("getter.contract.int")
    assert result is None


# STRING getter on stored double returns the default
def test_string_getter_on_stored_double_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("getter.contract.double", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on stored double with no default returns nil
def test_string_getter_on_stored_double_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("getter.contract.double")
    assert result is None


# STRING getter on stored bool returns the default
def test_string_getter_on_stored_bool_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("getter.contract.bool", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on stored bool with no default returns nil
def test_string_getter_on_stored_bool_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("getter.contract.bool")
    assert result is None


# STRING getter on stored string_list returns the default
def test_string_getter_on_stored_string_list_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("getter.contract.string_list", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on stored string_list with no default returns nil
def test_string_getter_on_stored_string_list_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("getter.contract.string_list")
    assert result is None


# STRING getter on stored json returns the default
def test_string_getter_on_stored_json_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("getter.contract.json", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on stored json with no default returns nil
def test_string_getter_on_stored_json_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("getter.contract.json")
    assert result is None


# STRING getter on stored duration returns the default
def test_string_getter_on_stored_duration_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("getter.contract.duration", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on stored duration with no default returns nil
def test_string_getter_on_stored_duration_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("getter.contract.duration")
    assert result is None


# INT getter on stored string returns the default
def test_int_getter_on_stored_string_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.string", default=7)
    assert result == 7


# INT getter on stored string with no default returns nil
def test_int_getter_on_stored_string_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.string")
    assert result is None


# INT getter on stored double 3.9 returns the default
def test_int_getter_on_stored_double_3_9_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.double", default=7)
    assert result == 7


# INT getter on stored double 3.9 with no default returns nil
def test_int_getter_on_stored_double_3_9_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.double")
    assert result is None


# INT getter on stored bool returns the default
def test_int_getter_on_stored_bool_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.bool", default=7)
    assert result == 7


# INT getter on stored bool with no default returns nil
def test_int_getter_on_stored_bool_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.bool")
    assert result is None


# INT getter on stored string_list returns the default
def test_int_getter_on_stored_string_list_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.string_list", default=7)
    assert result == 7


# INT getter on stored string_list with no default returns nil
def test_int_getter_on_stored_string_list_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.string_list")
    assert result is None


# INT getter on stored json returns the default
def test_int_getter_on_stored_json_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.json", default=7)
    assert result == 7


# INT getter on stored json with no default returns nil
def test_int_getter_on_stored_json_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.json")
    assert result is None


# INT getter on stored duration returns the default
def test_int_getter_on_stored_duration_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.duration", default=7)
    assert result == 7


# INT getter on stored duration with no default returns nil
def test_int_getter_on_stored_duration_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.duration")
    assert result is None


# DOUBLE getter on stored string returns the default
def test_double_getter_on_stored_string_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.string", default=7.5)
    assert abs(result - 7.5) < 1e-9


# DOUBLE getter on stored string with no default returns nil
def test_double_getter_on_stored_string_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_float("getter.contract.string")
    assert result is None


# DOUBLE getter on stored bool returns the default
def test_double_getter_on_stored_bool_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.bool", default=7.5)
    assert abs(result - 7.5) < 1e-9


# DOUBLE getter on stored bool with no default returns nil
def test_double_getter_on_stored_bool_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_float("getter.contract.bool")
    assert result is None


# DOUBLE getter on stored string_list returns the default
def test_double_getter_on_stored_string_list_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.string_list", default=7.5)
    assert abs(result - 7.5) < 1e-9


# DOUBLE getter on stored string_list with no default returns nil
def test_double_getter_on_stored_string_list_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_float("getter.contract.string_list")
    assert result is None


# DOUBLE getter on stored json returns the default
def test_double_getter_on_stored_json_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.json", default=7.5)
    assert abs(result - 7.5) < 1e-9


# DOUBLE getter on stored json with no default returns nil
def test_double_getter_on_stored_json_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_float("getter.contract.json")
    assert result is None


# DOUBLE getter on stored duration returns the default
def test_double_getter_on_stored_duration_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.duration", default=7.5)
    assert abs(result - 7.5) < 1e-9


# DOUBLE getter on stored duration with no default returns nil
def test_double_getter_on_stored_duration_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_float("getter.contract.duration")
    assert result is None


# BOOLEAN getter on stored string returns the default
def test_boolean_getter_on_stored_string_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.string", default=False)
    assert result is False


# BOOLEAN getter on stored string with no default returns nil
def test_boolean_getter_on_stored_string_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.string")
    assert result is None


# BOOLEAN getter on stored int returns the default
def test_boolean_getter_on_stored_int_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.int", default=False)
    assert result is False


# BOOLEAN getter on stored int with no default returns nil
def test_boolean_getter_on_stored_int_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.int")
    assert result is None


# BOOLEAN getter on stored double returns the default
def test_boolean_getter_on_stored_double_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.double", default=False)
    assert result is False


# BOOLEAN getter on stored double with no default returns nil
def test_boolean_getter_on_stored_double_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.double")
    assert result is None


# BOOLEAN getter on stored string_list returns the default
def test_boolean_getter_on_stored_string_list_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.string_list", default=False)
    assert result is False


# BOOLEAN getter on stored string_list with no default returns nil
def test_boolean_getter_on_stored_string_list_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.string_list")
    assert result is None


# BOOLEAN getter on stored json returns the default
def test_boolean_getter_on_stored_json_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.json", default=False)
    assert result is False


# BOOLEAN getter on stored json with no default returns nil
def test_boolean_getter_on_stored_json_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.json")
    assert result is None


# BOOLEAN getter on stored duration returns the default
def test_boolean_getter_on_stored_duration_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.duration", default=False)
    assert result is False


# BOOLEAN getter on stored duration with no default returns nil
def test_boolean_getter_on_stored_duration_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.duration")
    assert result is None


# STRING_LIST getter on stored string returns the default
def test_string_list_getter_on_stored_string_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string_list("getter.contract.string", default=["DEFAULT"])
    assert result == ["DEFAULT"]


# STRING_LIST getter on stored string with no default returns nil
def test_string_list_getter_on_stored_string_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string_list("getter.contract.string")
    assert result is None


# STRING_LIST getter on stored int returns the default
def test_string_list_getter_on_stored_int_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string_list("getter.contract.int", default=["DEFAULT"])
    assert result == ["DEFAULT"]


# STRING_LIST getter on stored int with no default returns nil
def test_string_list_getter_on_stored_int_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string_list("getter.contract.int")
    assert result is None


# STRING_LIST getter on stored double returns the default
def test_string_list_getter_on_stored_double_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string_list("getter.contract.double", default=["DEFAULT"])
    assert result == ["DEFAULT"]


# STRING_LIST getter on stored double with no default returns nil
def test_string_list_getter_on_stored_double_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string_list("getter.contract.double")
    assert result is None


# STRING_LIST getter on stored bool returns the default
def test_string_list_getter_on_stored_bool_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string_list("getter.contract.bool", default=["DEFAULT"])
    assert result == ["DEFAULT"]


# STRING_LIST getter on stored bool with no default returns nil
def test_string_list_getter_on_stored_bool_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string_list("getter.contract.bool")
    assert result is None


# STRING_LIST getter on stored json returns the default
def test_string_list_getter_on_stored_json_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string_list("getter.contract.json", default=["DEFAULT"])
    assert result == ["DEFAULT"]


# STRING_LIST getter on stored json with no default returns nil
def test_string_list_getter_on_stored_json_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string_list("getter.contract.json")
    assert result is None


# STRING_LIST getter on stored duration returns the default
def test_string_list_getter_on_stored_duration_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string_list("getter.contract.duration", default=["DEFAULT"])
    assert result == ["DEFAULT"]


# STRING_LIST getter on stored duration with no default returns nil
def test_string_list_getter_on_stored_duration_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string_list("getter.contract.duration")
    assert result is None


# JSON getter on stored string returns the default
def test_json_getter_on_stored_string_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_json("getter.contract.string", default={"default": True})
    assert result == {"default": True}


# JSON getter on stored string with no default returns nil
def test_json_getter_on_stored_string_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_json("getter.contract.string")
    assert result is None


# JSON getter on stored int returns the default
def test_json_getter_on_stored_int_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_json("getter.contract.int", default={"default": True})
    assert result == {"default": True}


# JSON getter on stored int with no default returns nil
def test_json_getter_on_stored_int_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_json("getter.contract.int")
    assert result is None


# JSON getter on stored double returns the default
def test_json_getter_on_stored_double_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_json("getter.contract.double", default={"default": True})
    assert result == {"default": True}


# JSON getter on stored double with no default returns nil
def test_json_getter_on_stored_double_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_json("getter.contract.double")
    assert result is None


# JSON getter on stored bool returns the default
def test_json_getter_on_stored_bool_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_json("getter.contract.bool", default={"default": True})
    assert result == {"default": True}


# JSON getter on stored bool with no default returns nil
def test_json_getter_on_stored_bool_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_json("getter.contract.bool")
    assert result is None


# JSON getter on stored string_list returns the default
def test_json_getter_on_stored_string_list_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_json("getter.contract.string_list", default={"default": True})
    assert result == {"default": True}


# JSON getter on stored string_list with no default returns nil
def test_json_getter_on_stored_string_list_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_json("getter.contract.string_list")
    assert result is None


# JSON getter on stored duration returns the default
def test_json_getter_on_stored_duration_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_json("getter.contract.duration", default={"default": True})
    assert result == {"default": True}


# JSON getter on stored duration with no default returns nil
def test_json_getter_on_stored_duration_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_json("getter.contract.duration")
    assert result is None


# DURATION getter on stored string returns the default
def test_duration_getter_on_stored_string_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("getter.contract.string", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# DURATION getter on stored string with no default returns nil
def test_duration_getter_on_stored_string_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("getter.contract.string")
    assert result is None


# DURATION getter on stored int returns the default
def test_duration_getter_on_stored_int_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("getter.contract.int", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# DURATION getter on stored int with no default returns nil
def test_duration_getter_on_stored_int_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("getter.contract.int")
    assert result is None


# DURATION getter on stored double returns the default
def test_duration_getter_on_stored_double_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("getter.contract.double", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# DURATION getter on stored double with no default returns nil
def test_duration_getter_on_stored_double_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("getter.contract.double")
    assert result is None


# DURATION getter on stored bool returns the default
def test_duration_getter_on_stored_bool_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("getter.contract.bool", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# DURATION getter on stored bool with no default returns nil
def test_duration_getter_on_stored_bool_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("getter.contract.bool")
    assert result is None


# DURATION getter on stored string_list returns the default
def test_duration_getter_on_stored_string_list_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("getter.contract.string_list", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# DURATION getter on stored string_list with no default returns nil
def test_duration_getter_on_stored_string_list_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("getter.contract.string_list")
    assert result is None


# DURATION getter on stored json returns the default
def test_duration_getter_on_stored_json_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("getter.contract.json", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# DURATION getter on stored json with no default returns nil
def test_duration_getter_on_stored_json_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("getter.contract.json")
    assert result is None


# BOOLEAN getter on stored string "false" returns the default
def test_boolean_getter_on_stored_string_false_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.string.false", default=False)
    assert result is False


# BOOLEAN getter on stored string "false" with no default returns nil
def test_boolean_getter_on_stored_string_false_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.string.false")
    assert result is None


# BOOLEAN getter on stored string "false" returns a true default
def test_boolean_getter_on_stored_string_false_returns_a_true_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.string.false", default=True)
    assert result is True


# BOOLEAN getter on stored string "true" returns the default
def test_boolean_getter_on_stored_string_true_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.string.true", default=False)
    assert result is False


# BOOLEAN getter on stored string "true" with no default returns nil
def test_boolean_getter_on_stored_string_true_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.string.true")
    assert result is None


# INT getter on stored string "3" returns the default
def test_int_getter_on_stored_string_3_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.string.number", default=7)
    assert result == 7


# INT getter on stored string "3" with no default returns nil
def test_int_getter_on_stored_string_3_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.string.number")
    assert result is None


# DOUBLE getter on stored string "3" returns the default
def test_double_getter_on_stored_string_3_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.string.number", default=7.5)
    assert abs(result - 7.5) < 1e-9


# DOUBLE getter on stored string "3" with no default returns nil
def test_double_getter_on_stored_string_3_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_float("getter.contract.string.number")
    assert result is None


# DURATION getter on stored string "PT1S" returns the default
def test_duration_getter_on_stored_string_pt1s_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("getter.contract.string.duration", default=7)
    assert result == 7000 / 1000, f"Expected exactly 7000ms (7000 / 1000 s), got {result!r}s"


# DURATION getter on stored string "PT1S" with no default returns nil
def test_duration_getter_on_stored_string_pt1s_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("getter.contract.string.duration")
    assert result is None


# DOUBLE get on stored int 30 returns 30.0
def test_double_get_on_stored_int_30_returns_30_0(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.int")
    assert result == 30


# INT get on stored whole double 3.0 returns 3
def test_int_get_on_stored_whole_double_3_0_returns_3(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.double.whole")
    assert result == 3


# DOUBLE getter on stored int 30 ignores the default
def test_double_getter_on_stored_int_30_ignores_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.int", default=7.5)
    assert result == 30


# INT getter on stored whole double 3.0 ignores the default
def test_int_getter_on_stored_whole_double_3_0_ignores_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.double.whole", default=7)
    assert result == 3


# INT getter on stored double 1e300 returns the default
def test_int_getter_on_stored_double_1e300_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.double.huge", default=7)
    assert result == 7


# INT getter on stored double 1e300 with no default returns nil
def test_int_getter_on_stored_double_1e300_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.double.huge")
    assert result is None


# INT getter on stored double 2^53 (outside the safe int range) returns the default
def test_int_getter_on_stored_double_2_53_outside_the_safe_int_range_returns_the_default(
    config_client,
) -> None:
    c = config_client
    result = c.get_int("getter.contract.double.unsafe", default=7)
    assert result == 7


# INT getter on stored double 2^53 (outside the safe int range) with no default returns nil
def test_int_getter_on_stored_double_2_53_outside_the_safe_int_range_with_no_default_returns_nil() -> (
    None
):
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.double.unsafe")
    assert result is None


# INT getter on stored malformed int "twelve" returns the default
def test_int_getter_on_stored_malformed_int_twelve_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_int("getter.contract.int.malformed", default=7)
    assert result == 7


# INT getter on stored malformed int "twelve" with no default returns nil
def test_int_getter_on_stored_malformed_int_twelve_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_int("getter.contract.int.malformed")
    assert result is None


# DOUBLE getter on stored malformed double "abc" returns the default
def test_double_getter_on_stored_malformed_double_abc_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_float("getter.contract.double.malformed", default=7.5)
    assert abs(result - 7.5) < 1e-9


# DOUBLE getter on stored malformed double "abc" with no default returns nil
def test_double_getter_on_stored_malformed_double_abc_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_float("getter.contract.double.malformed")
    assert result is None


# INT getter on env-var-provided malformed int "twelve" returns the default
def test_int_getter_on_env_var_provided_malformed_int_twelve_returns_the_default() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_GETTER_INT"] = os.environ.get("QUONFIG_ITD_GETTER_INT")
    os.environ["QUONFIG_ITD_GETTER_INT"] = "twelve"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_int("getter.contract.provided.int.malformed", default=7)
        assert result == 7
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# INT getter on env-var-provided malformed int "twelve" with no default returns nil
def test_int_getter_on_env_var_provided_malformed_int_twelve_with_no_default_returns_nil() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_GETTER_INT"] = os.environ.get("QUONFIG_ITD_GETTER_INT")
    os.environ["QUONFIG_ITD_GETTER_INT"] = "twelve"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
        c.init()
        result = c.get_int("getter.contract.provided.int.malformed")
        assert result is None
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# DOUBLE getter on env-var-provided malformed double "abc" returns the default
def test_double_getter_on_env_var_provided_malformed_double_abc_returns_the_default() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_GETTER_DOUBLE"] = os.environ.get("QUONFIG_ITD_GETTER_DOUBLE")
    os.environ["QUONFIG_ITD_GETTER_DOUBLE"] = "abc"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_float("getter.contract.provided.double.malformed", default=7.5)
        assert abs(result - 7.5) < 1e-9
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# DOUBLE getter on env-var-provided malformed double "abc" with no default returns nil
def test_double_getter_on_env_var_provided_malformed_double_abc_with_no_default_returns_nil() -> (
    None
):
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_GETTER_DOUBLE"] = os.environ.get("QUONFIG_ITD_GETTER_DOUBLE")
    os.environ["QUONFIG_ITD_GETTER_DOUBLE"] = "abc"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
        c.init()
        result = c.get_float("getter.contract.provided.double.malformed")
        assert result is None
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# STRING getter on a missing env var returns the default
def test_string_getter_on_a_missing_env_var_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("provided.by.missing.env.var", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on a missing env var with no default returns nil
def test_string_getter_on_a_missing_env_var_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("provided.by.missing.env.var")
    assert result is None


# STRING getter on a decryption failure returns the default
def test_string_getter_on_a_decryption_failure_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_string("a.broken.secret.config", default="DEFAULT")
    assert result == "DEFAULT"


# STRING getter on a decryption failure with no default returns nil
def test_string_getter_on_a_decryption_failure_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string("a.broken.secret.config")
    assert result is None


# BOOLEAN getter on a missing bool env var returns the default
def test_boolean_getter_on_a_missing_bool_env_var_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_bool("getter.contract.provided.bool.missing", default=True)
    assert result is True


# BOOLEAN getter on a missing bool env var with no default returns nil
def test_boolean_getter_on_a_missing_bool_env_var_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_bool("getter.contract.provided.bool.missing")
    assert result is None


# BOOLEAN getter on env-var-provided bool "false" returns false
def test_boolean_getter_on_env_var_provided_bool_false_returns_false() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ITD_GETTER_BOOL"] = os.environ.get("QUONFIG_ITD_GETTER_BOOL")
    os.environ["QUONFIG_ITD_GETTER_BOOL"] = "false"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
        c.init()
        result = c.get_bool("getter.contract.provided.bool")
        assert result is False
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
