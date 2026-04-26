# AUTO-GENERATED from integration-test-data/tests/eval/get_or_raise.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

import os

import pytest

from quonfig import Quonfig
from quonfig.exceptions import (
    QuonfigDecryptionError,
    QuonfigEnvVarNotSetError,
    QuonfigInitTimeoutError,
    QuonfigKeyNotFoundError,
)

DATADIR = os.path.join(
    os.path.dirname(__file__),
    "../../../integration-test-data/data/integration-tests",
)

# get_or_raise can raise an error if value not found
def test_get_or_raise_can_raise_an_error_if_value_not_found() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_string('my-missing-key')


# get_or_raise returns a default value instead of raising
def test_get_or_raise_returns_a_default_value_instead_of_raising() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    result = c.get_string('my-missing-key', default='DEFAULT')
    assert result == 'DEFAULT'


# get_or_raise raises the correct error if it doesn't raise on init timeout
def test_get_or_raise_raises_the_correct_error_if_it_doesn_t_raise_on_init_timeout() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error", initialization_timeout_sec=0.01, on_init_failure='return', prefab_api_url='https://app.staging-prefab.cloud')
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_string('any-key')


# get_or_raise can raise an error if the client does not initialize in time
def test_get_or_raise_can_raise_an_error_if_the_client_does_not_initialize_in_time() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error", initialization_timeout_sec=0.01, on_init_failure='raise', prefab_api_url='https://app.staging-prefab.cloud')
    c.init()
    with pytest.raises(QuonfigInitTimeoutError):
        c.get_string('any-key')


# raises an error if a config is provided by a missing environment variable
def test_raises_an_error_if_a_config_is_provided_by_a_missing_environment_variable() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigEnvVarNotSetError):
        c.get_string('provided.by.missing.env.var')


# raises an error if an env-var-provided config cannot be coerced to configured type
def test_raises_an_error_if_an_env_var_provided_config_cannot_be_coerced_to_configured_type() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigKeyNotFoundError):
        c.get_int('provided.not.a.number')


# raises an error for decryption failure
def test_raises_an_error_for_decryption_failure() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production", on_no_default="error")
    c.init()
    with pytest.raises(QuonfigDecryptionError):
        c.get_string('a.broken.secret.config')
