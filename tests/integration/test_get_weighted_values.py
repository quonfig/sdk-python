# AUTO-GENERATED from integration-test-data/tests/eval/get_weighted_values.yaml. DO NOT EDIT.
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


# weighted value is consistent 1
def test_weighted_value_is_consistent_1(config_client) -> None:
    c = config_client
    result = c.get_int('feature-flag.weighted', contexts={'user': {'tracking_id': 'a72c15f5'}})
    assert result == 1


# weighted value is consistent 2
def test_weighted_value_is_consistent_2(config_client) -> None:
    c = config_client
    result = c.get_int('feature-flag.weighted', contexts={'user': {'tracking_id': '92a202f2'}})
    assert result == 2


# weighted value is consistent 3
def test_weighted_value_is_consistent_3(config_client) -> None:
    c = config_client
    result = c.get_int('feature-flag.weighted', contexts={'user': {'tracking_id': '8f414100'}})
    assert result == 3
