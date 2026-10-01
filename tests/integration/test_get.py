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


# duration 200 ms
def test_duration_200_ms(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.2S")
    assert abs(result * 1000 - 200) < 1, f"Expected {result * 1000}ms to be close to 200ms"


# duration 90S
def test_duration_90s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT90S")
    assert abs(result * 1000 - 90000) < 1, f"Expected {result * 1000}ms to be close to 90000ms"


# duration 30M
def test_duration_30m(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT30M")
    assert abs(result * 1000 - 1800000) < 1, f"Expected {result * 1000}ms to be close to 1800000ms"


# duration test.duration.P1DT6H2M1.5S
def test_duration_test_duration_p1dt6h2m1_5s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P1DT6H2M1.5S")
    assert abs(result * 1000 - 108121500) < 1, (
        f"Expected {result * 1000}ms to be close to 108121500ms"
    )


# duration zero PT0S
def test_duration_zero_pt0s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0S")
    assert abs(result * 1000 - 0) < 1, f"Expected {result * 1000}ms to be close to 0ms"


# duration zero P0D
def test_duration_zero_p0d(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P0D")
    assert abs(result * 1000 - 0) < 1, f"Expected {result * 1000}ms to be close to 0ms"


# duration days only P2D
def test_duration_days_only_p2d(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P2D")
    assert abs(result * 1000 - 172800000) < 1, (
        f"Expected {result * 1000}ms to be close to 172800000ms"
    )


# duration hours only PT1H
def test_duration_hours_only_pt1h(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1H")
    assert abs(result * 1000 - 3600000) < 1, f"Expected {result * 1000}ms to be close to 3600000ms"


# duration minutes only PT1M
def test_duration_minutes_only_pt1m(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1M")
    assert abs(result * 1000 - 60000) < 1, f"Expected {result * 1000}ms to be close to 60000ms"


# duration seconds only PT1S
def test_duration_seconds_only_pt1s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1S")
    assert abs(result * 1000 - 1000) < 1, f"Expected {result * 1000}ms to be close to 1000ms"


# duration leading zero PT05S
def test_duration_leading_zero_pt05s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT05S")
    assert abs(result * 1000 - 5000) < 1, f"Expected {result * 1000}ms to be close to 5000ms"


# duration hours and minutes PT1H30M
def test_duration_hours_and_minutes_pt1h30m(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1H30M")
    assert abs(result * 1000 - 5400000) < 1, f"Expected {result * 1000}ms to be close to 5400000ms"


# duration days and hours P1DT2H
def test_duration_days_and_hours_p1dt2h(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P1DT2H")
    assert abs(result * 1000 - 93600000) < 1, (
        f"Expected {result * 1000}ms to be close to 93600000ms"
    )


# duration one millisecond PT0.001S
def test_duration_one_millisecond_pt0_001s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.001S")
    assert abs(result * 1000 - 1) < 1, f"Expected {result * 1000}ms to be close to 1ms"


# duration magnitude ceiling P36500D
def test_duration_magnitude_ceiling_p36500d(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P36500D")
    assert abs(result * 1000 - 3153600000000) < 1, (
        f"Expected {result * 1000}ms to be close to 3153600000000ms"
    )


# duration rounding PT2.01S
def test_duration_rounding_pt2_01s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT2.01S")
    assert abs(result * 1000 - 2010) < 1, f"Expected {result * 1000}ms to be close to 2010ms"


# duration rounding PT1.005S
def test_duration_rounding_pt1_005s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1.005S")
    assert abs(result * 1000 - 1005) < 1, f"Expected {result * 1000}ms to be close to 1005ms"


# duration rounding half up PT0.0005S
def test_duration_rounding_half_up_pt0_0005s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.0005S")
    assert abs(result * 1000 - 1) < 1, f"Expected {result * 1000}ms to be close to 1ms"


# duration rounding down PT0.0004S
def test_duration_rounding_down_pt0_0004s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.0004S")
    assert abs(result * 1000 - 0) < 1, f"Expected {result * 1000}ms to be close to 0ms"


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
        assert abs(result * 1000 - 1500) < 1, f"Expected {result * 1000}ms to be close to 1500ms"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# stored malformed duration 30s returns the default
def test_stored_malformed_duration_30s_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.30s", default=7000)
    assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"


# stored malformed duration 30s with no default returns nil
def test_stored_malformed_duration_30s_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.30s")
    assert result is None


# stored malformed duration PT0.5H returns the default
def test_stored_malformed_duration_pt0_5h_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.PT0.5H", default=7000)
    assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"


# stored malformed duration PT0.5H with no default returns nil
def test_stored_malformed_duration_pt0_5h_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.PT0.5H")
    assert result is None


# stored malformed duration P1DT returns the default
def test_stored_malformed_duration_p1dt_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.P1DT", default=7000)
    assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"


# stored malformed duration P1DT with no default returns nil
def test_stored_malformed_duration_p1dt_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.P1DT")
    assert result is None


# stored malformed duration garbage returns the default
def test_stored_malformed_duration_garbage_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.garbage", default=7000)
    assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"


# stored malformed duration garbage with no default returns nil
def test_stored_malformed_duration_garbage_with_no_default_returns_nil() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_duration("test.duration.malformed.garbage")
    assert result is None


# stored malformed duration empty returns the default
def test_stored_malformed_duration_empty_returns_the_default(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.malformed.empty", default=7000)
    assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"


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
        result = c.get_duration("provided.duration.malformed.30s", default=7000)
        assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"
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
        result = c.get_duration("provided.duration.malformed.PT0.5H", default=7000)
        assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"
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
        result = c.get_duration("provided.duration.malformed.P1DT", default=7000)
        assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"
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
        result = c.get_duration("provided.duration.malformed.garbage", default=7000)
        assert abs(result * 1000 - 7000) < 1, f"Expected {result * 1000}ms to be close to 7000ms"
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
