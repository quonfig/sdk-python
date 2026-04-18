# AUTO-GENERATED from integration-test-data/tests/eval/get.yaml
# Do not edit by hand. Regenerate with:
#   python scripts/generate_integration_tests_python.py

import os

import pytest

from quonfig import Quonfig
from quonfig.exceptions import (
    QuonfigDecryptionError,
    QuonfigEnvVarNotSetError,
    QuonfigKeyNotFoundError,
)

DATADIR = os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests")

@pytest.fixture(scope="module")
def config_client():
    os.environ.setdefault(
        "PREFAB_INTEGRATION_TEST_ENCRYPTION_KEY",
        "c87ba22d8662282abe8a0e4651327b579cb64a454ab0f4c170b45b15f049a221",
    )
    os.environ.setdefault("IS_A_NUMBER", "1234")
    os.environ.setdefault("NOT_A_NUMBER", "not_a_number")
    os.environ.pop("MISSING_ENV_VAR", None)
    c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
    c.init()
    return c

def test_get_returns_a_found_value_for_key(config_client):
    c = config_client
    result = c.get_string('my-test-key')
    assert result == 'my-test-value'


def test_get_returns_nil_if_value_not_found():
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="warn")
    c.init()
    result = c.get_string('my-missing-key')
    assert result is None


def test_get_returns_a_default_for_a_missing_value_if_a_default_is_given(config_client):
    c = config_client
    result = c.get_string('my-missing-key', default='DEFAULT')
    assert result == 'DEFAULT'


def test_get_ignores_a_provided_default_if_the_key_is_found(config_client):
    c = config_client
    result = c.get_string('my-test-key', default='DEFAULT')
    assert result == 'my-test-value'


def test_get_can_return_a_double(config_client):
    c = config_client
    result = c.get_float('my-double-key')
    assert abs(result - 9.95) < 1e-9


def test_get_can_return_a_string_list(config_client):
    c = config_client
    result = c.get_string_list('my-string-list-key')
    assert result == ['a', 'b', 'c']


def test_can_return_an_override_based_on_the_default_context():
    pytest.skip("requires API-injected prefab-api-key context not available in local eval")


def test_can_return_a_value_provided_by_an_environment_variable(config_client):
    c = config_client
    result = c.get_string('prefab.secrets.encryption.key')
    assert result == 'c87ba22d8662282abe8a0e4651327b579cb64a454ab0f4c170b45b15f049a221'


def test_can_return_a_value_provided_by_an_environment_variable_after_type_coercion(config_client):
    c = config_client
    result = c.get_int('provided.a.number')
    assert result == 1234


def test_can_decrypt_and_return_a_secret_value_with_decryption_key_in_in_env_var(config_client):
    c = config_client
    result = c.get_string('a.secret.config')
    assert result == 'hello.world'


def test_duration_200_ms(config_client):
    c = config_client
    result = c.get_duration('test.duration.PT0.2S')
    assert abs(result * 1000 - 200) < 1, f"Expected {result * 1000}ms to be close to 200ms"


def test_duration_90s(config_client):
    c = config_client
    result = c.get_duration('test.duration.PT90S')
    assert abs(result * 1000 - 90000) < 1, f"Expected {result * 1000}ms to be close to 90000ms"


def test_duration_1_5m(config_client):
    c = config_client
    result = c.get_duration('test.duration.PT1.5M')
    assert abs(result * 1000 - 90000) < 1, f"Expected {result * 1000}ms to be close to 90000ms"


def test_duration_0_5h(config_client):
    c = config_client
    result = c.get_duration('test.duration.PT0.5H')
    assert abs(result * 1000 - 1800000) < 1, f"Expected {result * 1000}ms to be close to 1800000ms"


def test_duration_test_duration_p1dt6h2m1_5s(config_client):
    c = config_client
    result = c.get_duration('test.duration.P1DT6H2M1.5S')
    assert abs(result * 1000 - 108121500) < 1, f"Expected {result * 1000}ms to be close to 108121500ms"


def test_json_test(config_client):
    c = config_client
    result = c.get_json('test.json')
    assert result == {'a': 1, 'b': 'c'}


def test_get_returns_a_native_json_object_not_a_stringified_payload(config_client):
    c = config_client
    result = c.get_json('test.json')
    assert result == {'a': 1, 'b': 'c'}


def test_list_on_left_side_test_1(config_client):
    c = config_client
    result = c.get_string('left.hand.list.test', contexts={'user': {'name': 'james', 'aka': ['happy', 'sleepy']}})
    assert result == 'correct'


def test_list_on_left_side_test_2(config_client):
    c = config_client
    result = c.get_string('left.hand.list.test', contexts={'user': {'name': 'james', 'aka': ['a', 'b']}})
    assert result == 'default'


def test_list_on_left_side_test_opposite_1(config_client):
    c = config_client
    result = c.get_string('left.hand.test.opposite', contexts={'user': {'name': 'james', 'aka': ['happy', 'sleepy']}})
    assert result == 'default'


def test_list_on_left_side_test_3(config_client):
    c = config_client
    result = c.get_string('left.hand.test.opposite', contexts={'user': {'name': 'james', 'aka': ['a', 'b']}})
    assert result == 'correct'
