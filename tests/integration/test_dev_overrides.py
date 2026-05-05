# AUTO-GENERATED from integration-test-data/tests/eval/dev_overrides.yaml. DO NOT EDIT.
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


# override fires when quonfig-user.email matches
def test_override_fires_when_quonfig_user_email_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.dev-override", contexts={"quonfig-user": {"email": "bob@foo.com"}}
    )
    assert result is True


# override does not fire when attribute absent (prod simulation)
def test_override_does_not_fire_when_attribute_absent_prod_simulation(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.dev-override", contexts={"user": {"email": "bob@foo.com"}}
    )
    assert result is False


# override matches any email in IS_ONE_OF list
def test_override_matches_any_email_in_is_one_of_list(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.dev-override.multi-email",
        contexts={"quonfig-user": {"email": "alice@foo.com"}},
    )
    assert result is True


# override beats customer rule by priority
def test_override_beats_customer_rule_by_priority(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.dev-override.priority",
        contexts={"quonfig-user": {"email": "bob@foo.com"}, "user": {"country": "DE"}},
    )
    assert result is True
