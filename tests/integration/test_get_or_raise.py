# AUTO-GENERATED from integration-test-data/tests/eval/get_or_raise.yaml
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

DATADIR = os.path.join(
    os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests"
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
    c = Quonfig(datadir=DATADIR, environment="Production", on_init_failure="return_zero_value")
    c.init()
    return c


def test_get_or_raise_can_raise_an_error_if_value_not_found():
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_string("my-missing-key")


def test_get_or_raise_returns_a_default_value_instead_of_raising():
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    result = c.get_string("my-missing-key", default="DEFAULT")
    assert result == "DEFAULT"


def test_get_or_raise_raises_the_correct_error_if_it_doesn_t_raise_on_init_timeout():
    pytest.skip("initialization_timeout tests require async or subprocess")


def test_get_or_raise_can_raise_an_error_if_the_client_does_not_initialize_in_time():
    pytest.skip("initialization_timeout tests require async or subprocess")


def test_raises_an_error_if_a_config_is_provided_by_a_missing_environment_variable():
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigEnvVarNotSetError):
        c.get_string("provided.by.missing.env.var")


def test_raises_an_error_if_an_env_var_provided_config_cannot_be_coerced_to_configured_type():
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_int("provided.not.a.number")


def test_raises_an_error_for_decryption_failure():
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigDecryptionError):
        c.get_string("a.broken.secret.config")
