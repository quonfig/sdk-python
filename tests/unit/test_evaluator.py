"""Tests for the Evaluator (rule matching, environment selection, etc.)"""

from __future__ import annotations

from quonfig.evaluator import Evaluator
from quonfig.store import ConfigStore
from quonfig.types import (
    ConfigEnvelope,
    ConfigResponse,
    Criterion,
    Environment,
    Meta,
    Rule,
    RuleSet,
    Value,
)


def make_string_value(val: str) -> Value:
    return Value(type="string", value=val)


def make_bool_value(val: bool) -> Value:
    return Value(type="bool", value=val)


def make_always_true_rule(value: Value) -> Rule:
    return Rule(criteria=[Criterion(operator="ALWAYS_TRUE")], value=value)


def make_criterion_rule(
    operator: str, property_name: str, match_value, value: Value, vtype: str = "string"
) -> Rule:
    vtm = Value(type=vtype, value=match_value)
    criterion = Criterion(operator=operator, property_name=property_name, value_to_match=vtm)
    return Rule(criteria=[criterion], value=value)


def make_store(configs) -> ConfigStore:
    store = ConfigStore()
    envelope = ConfigEnvelope(
        configs=configs,
        meta=Meta(version="test", environment="test"),
    )
    store.update(envelope)
    return store


class TestBasicEvaluation:
    def test_missing_key_returns_missing(self):
        store = make_store([])
        evaluator = Evaluator(store, "Production")
        result = evaluator.evaluate("nonexistent.key", {})
        assert result.reason == "MISSING"
        assert result.value is None

    def test_always_true_rule_matches(self):
        config = ConfigResponse(
            id="1",
            key="my.flag",
            type="feature_flag",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(rules=[make_always_true_rule(make_bool_value(True))]),
        )
        store = make_store([config])
        evaluator = Evaluator(store, "Production")
        result = evaluator.evaluate("my.flag", {})
        assert result.reason == "DEFAULT"
        assert result.value is not None
        assert result.value.value is True

    def test_no_matching_rule_returns_missing(self):
        # A rule that checks user.plan == "pro" — won't match if plan is "free"
        config = ConfigResponse(
            id="1",
            key="my.flag",
            type="feature_flag",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    make_criterion_rule(
                        "PROP_IS_ONE_OF", "user.plan", ["pro"], make_bool_value(True)
                    )
                ]
            ),
        )
        store = make_store([config])
        evaluator = Evaluator(store, "Production")
        result = evaluator.evaluate("my.flag", {"user": {"plan": "free"}})
        assert result.reason == "MISSING"

    def test_first_matching_rule_wins(self):
        config = ConfigResponse(
            id="1",
            key="my.key",
            type="config",
            value_type="string",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    make_criterion_rule(
                        "PROP_IS_ONE_OF", "user.plan", ["pro"], make_string_value("pro-value")
                    ),
                    make_always_true_rule(make_string_value("default-value")),
                ]
            ),
        )
        store = make_store([config])
        evaluator = Evaluator(store, "Production")

        # Pro user gets the first matching rule
        result = evaluator.evaluate("my.key", {"user": {"plan": "pro"}})
        assert result.value.value == "pro-value"

        # Free user falls through to always-true
        result = evaluator.evaluate("my.key", {"user": {"plan": "free"}})
        assert result.value.value == "default-value"


class TestEnvironmentSelection:
    def test_environment_rules_take_priority(self):
        config = ConfigResponse(
            id="1",
            key="my.flag",
            type="feature_flag",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(rules=[make_always_true_rule(make_bool_value(False))]),
            environment=Environment(
                id="Production",
                rules=[make_always_true_rule(make_bool_value(True))],
            ),
        )
        store = make_store([config])

        # Evaluating in Production: env rules take priority
        evaluator = Evaluator(store, "Production")
        result = evaluator.evaluate("my.flag", {})
        assert result.reason == "RULE_MATCH"
        assert result.value.value is True

    def test_wrong_environment_falls_through_to_default(self):
        config = ConfigResponse(
            id="1",
            key="my.flag",
            type="feature_flag",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(rules=[make_always_true_rule(make_bool_value(False))]),
            environment=Environment(
                id="Staging",
                rules=[make_always_true_rule(make_bool_value(True))],
            ),
        )
        store = make_store([config])

        # Evaluating in Production: env is Staging, so use default
        evaluator = Evaluator(store, "Production")
        result = evaluator.evaluate("my.flag", {})
        assert result.reason == "DEFAULT"
        assert result.value.value is False

    def test_no_environment_config_uses_default(self):
        config = ConfigResponse(
            id="1",
            key="my.key",
            type="config",
            value_type="string",
            send_to_client_sdk=True,
            default=RuleSet(rules=[make_always_true_rule(make_string_value("default"))]),
            environment=None,
        )
        store = make_store([config])
        evaluator = Evaluator(store, "Production")
        result = evaluator.evaluate("my.key", {})
        assert result.reason == "DEFAULT"
        assert result.value.value == "default"


class TestContextMatching:
    def test_prop_is_one_of_match(self):
        config = ConfigResponse(
            id="1",
            key="my.flag",
            type="feature_flag",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    make_criterion_rule(
                        "PROP_IS_ONE_OF",
                        "user.email",
                        ["alice@example.com"],
                        make_bool_value(True),
                        vtype="string_list",
                    ),
                    make_always_true_rule(make_bool_value(False)),
                ]
            ),
        )
        store = make_store([config])
        evaluator = Evaluator(store, "Production")

        result = evaluator.evaluate("my.flag", {"user": {"email": "alice@example.com"}})
        assert result.value.value is True

        result = evaluator.evaluate("my.flag", {"user": {"email": "bob@example.com"}})
        assert result.value.value is False

    def test_multiple_criteria_all_must_match(self):
        # Rule matches when BOTH user.plan == "pro" AND user.country == "US"
        rule_with_two_criteria = Rule(
            criteria=[
                Criterion(
                    operator="PROP_IS_ONE_OF",
                    property_name="user.plan",
                    value_to_match=Value(type="string_list", value=["pro"]),
                ),
                Criterion(
                    operator="PROP_IS_ONE_OF",
                    property_name="user.country",
                    value_to_match=Value(type="string_list", value=["US"]),
                ),
            ],
            value=make_bool_value(True),
        )
        config = ConfigResponse(
            id="1",
            key="my.flag",
            type="feature_flag",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(rules=[rule_with_two_criteria]),
        )
        store = make_store([config])
        evaluator = Evaluator(store, "Production")

        # Both match
        result = evaluator.evaluate("my.flag", {"user": {"plan": "pro", "country": "US"}})
        assert result.value.value is True

        # Only plan matches
        result = evaluator.evaluate("my.flag", {"user": {"plan": "pro", "country": "UK"}})
        assert result.reason == "MISSING"


class TestRowIndex:
    def test_row_index_set_correctly(self):
        config = ConfigResponse(
            id="1",
            key="my.key",
            type="config",
            value_type="string",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    make_criterion_rule(
                        "PROP_IS_ONE_OF", "user.plan", ["pro"], make_string_value("pro-val")
                    ),
                    make_always_true_rule(make_string_value("default-val")),
                ]
            ),
        )
        store = make_store([config])
        evaluator = Evaluator(store, "Production")

        result = evaluator.evaluate("my.key", {"user": {"plan": "pro"}})
        assert result.row_index == 0  # first rule

        result = evaluator.evaluate("my.key", {"user": {"plan": "free"}})
        assert result.row_index == 1  # second rule (always_true)
