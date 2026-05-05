# AUTO-GENERATED from integration-test-data/tests/eval/enabled_with_contexts.yaml. DO NOT EDIT.
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


# returns true from global context
def test_returns_true_from_global_context(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "prefab.cloud"}, "user": {"key": "michael"}},
    )
    assert result is True


# returns false due to local context override
def test_returns_false_due_to_local_context_override(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "prefab.cloud"}, "user": {"key": "james"}},
    )
    assert result is False


# returns false for untouched scope context
def test_returns_false_for_untouched_scope_context(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "example.com"}, "user": {"key": "nobody"}},
    )
    assert result is False


# returns false due to partial scope context override of user.key
def test_returns_false_due_to_partial_scope_context_override_of_user_key(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "example.com"}, "user": {"key": "michael"}},
    )
    assert result is False


# returns false due to partial scope context override of domain
def test_returns_false_due_to_partial_scope_context_override_of_domain(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "example.com", "key": "prefab.cloud"}, "user": {"key": "nobody"}},
    )
    assert result is False


# returns true due to full scope context override of user.key and domain
def test_returns_true_due_to_full_scope_context_override_of_user_key_and_domain(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "prefab.cloud"}, "user": {"key": "michael"}},
    )
    assert result is True


# returns false for rule with different case on context property name
def test_returns_false_for_rule_with_different_case_on_context_property_name(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"IsHuman": "verified"}}
    )
    assert result is False


# returns true for matching case on context property name
def test_returns_true_for_matching_case_on_context_property_name(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"isHuman": "verified"}}
    )
    assert result is True
