"""Shared test fixtures for the Quonfig SDK unit tests."""
from __future__ import annotations

import pytest

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


def make_value(vtype: str, val, confidential: bool = False, decrypt_with=None) -> Value:
    return Value(type=vtype, value=val, confidential=confidential, decrypt_with=decrypt_with)


def make_bool_value(val: bool) -> Value:
    return make_value("bool", val)


def make_string_value(val: str) -> Value:
    return make_value("string", val)


def make_int_value(val: int) -> Value:
    return make_value("int", val)


def make_criterion(operator: str, property_name: str = None, value=None, vtype: str = "string") -> Criterion:
    vtm = make_value(vtype, value) if value is not None else None
    return Criterion(operator=operator, property_name=property_name, value_to_match=vtm)


def make_always_true_rule(value: Value) -> Rule:
    return Rule(criteria=[make_criterion("ALWAYS_TRUE")], value=value)


def make_config(
    key: str,
    default_rules: list[Rule],
    environment_id: str = None,
    environment_rules: list[Rule] = None,
    value_type: str = "string",
    config_type: str = "config",
    config_id: str = "cfg-1",
) -> ConfigResponse:
    default = RuleSet(rules=default_rules)
    env = None
    if environment_id and environment_rules is not None:
        env = Environment(id=environment_id, rules=environment_rules)
    return ConfigResponse(
        id=config_id,
        key=key,
        type=config_type,
        value_type=value_type,
        send_to_client_sdk=True,
        default=default,
        environment=env,
    )


def make_store_with_configs(configs: list[ConfigResponse]) -> ConfigStore:
    store = ConfigStore()
    envelope = ConfigEnvelope(
        configs=configs,
        meta=Meta(version="test", environment="test"),
    )
    store.update(envelope)
    return store


@pytest.fixture
def empty_store() -> ConfigStore:
    return ConfigStore()


@pytest.fixture
def make_value_fn():
    return make_value


@pytest.fixture
def make_config_fn():
    return make_config


@pytest.fixture
def make_store_fn():
    return make_store_with_configs
