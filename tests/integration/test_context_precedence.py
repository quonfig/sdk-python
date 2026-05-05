# AUTO-GENERATED from integration-test-data/tests/eval/context_precedence.yaml. DO NOT EDIT.
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


# returns the correct `flag` value using the global context (1)
def test_returns_the_correct_flag_value_using_the_global_context_1(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"isHuman": "verified"}}
    )
    assert result is True


# returns the correct `flag` value using the global context (2)
def test_returns_the_correct_flag_value_using_the_global_context_2(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("mixed.case.property.name", contexts={"user": {"isHuman": "?"}})
    assert result is False


# returns the correct `flag` value when local context clobbers global context (1)
def test_returns_the_correct_flag_value_when_local_context_clobbers_global_context_1(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"isHuman": "verified"}}
    )
    assert result is True


# returns the correct `flag` value when local context clobbers global context (2)
def test_returns_the_correct_flag_value_when_local_context_clobbers_global_context_2(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("mixed.case.property.name", contexts={"user": {"isHuman": "?"}})
    assert result is False


# returns the correct `flag` value when block context clobbers global context (1)
def test_returns_the_correct_flag_value_when_block_context_clobbers_global_context_1(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("mixed.case.property.name", contexts={"user": {"isHuman": "?"}})
    assert result is False


# returns the correct `flag` value when block context clobbers global context (2)
def test_returns_the_correct_flag_value_when_block_context_clobbers_global_context_2(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"isHuman": "verified"}}
    )
    assert result is True


# returns the correct `flag` value when local context clobbers block context (1)
def test_returns_the_correct_flag_value_when_local_context_clobbers_block_context_1(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("mixed.case.property.name", contexts={"user": {"isHuman": "?"}})
    assert result is False


# returns the correct `flag` value when local context clobbers block context (2)
def test_returns_the_correct_flag_value_when_local_context_clobbers_block_context_2(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"isHuman": "verified"}}
    )
    assert result is True


# returns the correct `get` value using the global context (1)
def test_returns_the_correct_get_value_using_the_global_context_1(config_client) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@prefab.cloud"}})
    assert result == "override"


# returns the correct `get` value using the global context (2)
def test_returns_the_correct_get_value_using_the_global_context_2(config_client) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@example.com"}})
    assert result == "default"


# returns the correct `get` value when local context clobbers global context (1)
def test_returns_the_correct_get_value_when_local_context_clobbers_global_context_1(
    config_client,
) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@prefab.cloud"}})
    assert result == "override"


# returns the correct `get` value when local context clobbers global context (2)
def test_returns_the_correct_get_value_when_local_context_clobbers_global_context_2(
    config_client,
) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@example.com"}})
    assert result == "default"


# returns the correct `get` value when block context clobbers global context (1)
def test_returns_the_correct_get_value_when_block_context_clobbers_global_context_1(
    config_client,
) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@example.com"}})
    assert result == "default"


# returns the correct `get` value when block context clobbers global context (2)
def test_returns_the_correct_get_value_when_block_context_clobbers_global_context_2(
    config_client,
) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@prefab.cloud"}})
    assert result == "override"


# returns the correct `get` value when local context clobbers block context (1)
def test_returns_the_correct_get_value_when_local_context_clobbers_block_context_1(
    config_client,
) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@example.com"}})
    assert result == "default"


# returns the correct `get` value when local context clobbers block context (2)
def test_returns_the_correct_get_value_when_local_context_clobbers_block_context_2(
    config_client,
) -> None:
    c = config_client
    result = c.get_string("basic.rule.config", contexts={"user": {"email": "test@prefab.cloud"}})
    assert result == "override"
