# AUTO-GENERATED from integration-test-data/tests/eval/context_precedence.yaml
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

def test_returns_the_correct_flag_value_using_the_global_context_1(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': 'verified'}})
    assert result is True


def test_returns_the_correct_flag_value_using_the_global_context_2(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': '?'}})
    assert result is False


def test_returns_the_correct_flag_value_when_local_context_clobbers_global_context_1(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': 'verified'}})
    assert result is True


def test_returns_the_correct_flag_value_when_local_context_clobbers_global_context_2(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': '?'}})
    assert result is False


def test_returns_the_correct_flag_value_when_block_context_clobbers_global_context_1(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': '?'}})
    assert result is False


def test_returns_the_correct_flag_value_when_block_context_clobbers_global_context_2(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': 'verified'}})
    assert result is True


def test_returns_the_correct_flag_value_when_local_context_clobbers_block_context_1(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': '?'}})
    assert result is False


def test_returns_the_correct_flag_value_when_local_context_clobbers_block_context_2(config_client):
    c = config_client
    result = c.is_feature_enabled('mixed.case.property.name', contexts={'user': {'isHuman': 'verified'}})
    assert result is True


def test_returns_the_correct_get_value_using_the_global_context_1(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@prefab.cloud'}})
    assert result == 'override'


def test_returns_the_correct_get_value_using_the_global_context_2(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@example.com'}})
    assert result == 'default'


def test_returns_the_correct_get_value_using_the_global_context_and_api_context_1(config_client):
    c = config_client
    result = c.get_string('basic.rule.config.with.api.conditional', contexts={'user': {'email': 'test@prefab.cloud'}})
    assert result == 'override'


def test_returns_the_correct_get_value_using_the_global_context_and_api_context_2():
    pytest.skip("requires API-injected prefab-api-key context not available in local eval")


def test_returns_the_correct_get_value_when_local_context_clobbers_global_context_1(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@prefab.cloud'}})
    assert result == 'override'


def test_returns_the_correct_get_value_when_local_context_clobbers_global_context_2(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@example.com'}})
    assert result == 'default'


def test_returns_the_correct_get_value_when_block_context_clobbers_global_context_1(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@example.com'}})
    assert result == 'default'


def test_returns_the_correct_get_value_when_block_context_clobbers_global_context_2(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@prefab.cloud'}})
    assert result == 'override'


def test_returns_the_correct_get_value_when_local_context_clobbers_block_context_1(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@example.com'}})
    assert result == 'default'


def test_returns_the_correct_get_value_when_local_context_clobbers_block_context_2(config_client):
    c = config_client
    result = c.get_string('basic.rule.config', contexts={'user': {'email': 'test@prefab.cloud'}})
    assert result == 'override'
