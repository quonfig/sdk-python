# AUTO-GENERATED from integration-test-data/tests/eval/get_feature_flag.yaml. DO NOT EDIT.
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


# get returns the underlying value for a feature flag
def test_get_returns_the_underlying_value_for_a_feature_flag(config_client) -> None:
    c = config_client
    result = c.get_int("feature-flag.integer")
    assert result == 3


# get returns the underlying value for a feature flag that matches the highest precedent rule
def test_get_returns_the_underlying_value_for_a_feature_flag_that_matches_the_highest_precedent_rule(
    config_client,
) -> None:
    c = config_client
    result = c.get_int("feature-flag.integer", contexts={"user": {"key": "michael"}})
    assert result == 5
