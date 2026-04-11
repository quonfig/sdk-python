# AUTO-GENERATED from integration-test-data/tests/eval/enabled_with_contexts.yaml
# Do not edit by hand. Regenerate with:
#   python scripts/generate_integration_tests_python.py

import os

import pytest

from quonfig import Quonfig

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


def test_returns_true_from_global_context(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "prefab.cloud"}, "user": {"key": "michael"}},
    )
    assert result is True


def test_returns_false_due_to_local_context_override(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "prefab.cloud"}, "user": {"key": "james"}},
    )
    assert result is False


def test_returns_false_for_untouched_scope_context(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "example.com"}, "user": {"key": "nobody"}},
    )
    assert result is False


def test_returns_false_due_to_partial_scope_context_override_of_user_key(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "example.com"}, "user": {"key": "michael"}},
    )
    assert result is False


def test_returns_false_due_to_partial_scope_context_override_of_domain(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "example.com", "key": "prefab.cloud"}, "user": {"key": "nobody"}},
    )
    assert result is False


def test_returns_true_due_to_full_scope_context_override_of_user_key_and_domain(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"": {"domain": "prefab.cloud"}, "user": {"key": "michael"}},
    )
    assert result is True


def test_returns_false_for_rule_with_different_case_on_context_property_name(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"IsHuman": "verified"}}
    )
    assert result is False


def test_returns_true_for_matching_case_on_context_property_name(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "mixed.case.property.name", contexts={"user": {"isHuman": "verified"}}
    )
    assert result is True
