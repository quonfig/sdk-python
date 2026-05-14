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


# duration 1.5M
def test_duration_1_5m(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT1.5M")
    assert abs(result * 1000 - 90000) < 1, f"Expected {result * 1000}ms to be close to 90000ms"


# duration 0.5H
def test_duration_0_5h(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.PT0.5H")
    assert abs(result * 1000 - 1800000) < 1, f"Expected {result * 1000}ms to be close to 1800000ms"


# duration test.duration.P1DT6H2M1.5S
def test_duration_test_duration_p1dt6h2m1_5s(config_client) -> None:
    c = config_client
    result = c.get_duration("test.duration.P1DT6H2M1.5S")
    assert abs(result * 1000 - 108121500) < 1, (
        f"Expected {result * 1000}ms to be close to 108121500ms"
    )


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
