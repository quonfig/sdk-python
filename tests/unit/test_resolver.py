"""Tests for Resolver: type coercion, ENV_VAR, weighted values, duration."""

from __future__ import annotations

import pytest

from quonfig.exceptions import QuonfigEnvVarNotSetError
from quonfig.resolver import Resolver
from quonfig.store import ConfigStore
from quonfig.types import Value


def make_store() -> ConfigStore:
    return ConfigStore()


def make_value(vtype: str, val, confidential=False, decrypt_with=None) -> Value:
    return Value(type=vtype, value=val, confidential=confidential, decrypt_with=decrypt_with)


class TestTypeCoercion:
    def setup_method(self):
        self.store = make_store()
        self.resolver = Resolver(self.store)

    def test_bool_true(self):
        v = make_value("bool", True)
        assert self.resolver.resolve(v, {}) is True

    def test_bool_false(self):
        v = make_value("bool", False)
        assert self.resolver.resolve(v, {}) is False

    def test_bool_from_string(self):
        v = make_value("bool", "true")
        assert self.resolver.resolve(v, {}) is True

    def test_int_from_int(self):
        v = make_value("int", 42)
        assert self.resolver.resolve(v, {}) == 42

    def test_int_from_string(self):
        v = make_value("int", "30")
        assert self.resolver.resolve(v, {}) == 30

    def test_double_from_float(self):
        v = make_value("double", 3.14)
        result = self.resolver.resolve(v, {})
        assert abs(result - 3.14) < 1e-9

    def test_double_from_int(self):
        v = make_value("double", 5)
        result = self.resolver.resolve(v, {})
        assert result == 5.0
        assert isinstance(result, float)

    def test_string_passthrough(self):
        v = make_value("string", "hello world")
        assert self.resolver.resolve(v, {}) == "hello world"

    def test_string_from_int(self):
        v = make_value("string", 42)
        assert self.resolver.resolve(v, {}) == "42"

    def test_json_dict_passthrough(self):
        data = {"key": "value", "nested": {"a": 1}}
        v = make_value("json", data)
        assert self.resolver.resolve(v, {}) == data

    def test_json_array_passthrough(self):
        data = [1, 2, {"a": "b"}]
        v = make_value("json", data)
        assert self.resolver.resolve(v, {}) == data

    def test_json_number_passthrough(self):
        v = make_value("json", 42)
        assert self.resolver.resolve(v, {}) == 42

    def test_json_bool_passthrough(self):
        v = make_value("json", True)
        assert self.resolver.resolve(v, {}) is True

    def test_json_null_passthrough(self):
        v = make_value("json", None)
        assert self.resolver.resolve(v, {}) is None

    def test_json_string_rejected(self):
        """Stringified JSON on wire is banned — must raise a clear error."""
        from quonfig.exceptions import QuonfigValueTypeError

        v = make_value("json", '{"key": "value"}')
        with pytest.raises(QuonfigValueTypeError, match="native JSON type"):
            self.resolver.resolve(v, {})

    def test_string_list(self):
        v = make_value("string_list", ["a", "b", "c"])
        result = self.resolver.resolve(v, {})
        assert result == ["a", "b", "c"]

    def test_string_list_coerces_items(self):
        v = make_value("string_list", [1, 2, 3])
        result = self.resolver.resolve(v, {})
        assert result == ["1", "2", "3"]

    def test_log_level_uppercase(self):
        v = make_value("log_level", "info")
        result = self.resolver.resolve(v, {})
        assert result == "INFO"

    def test_duration_iso8601(self):
        v = make_value("duration", "PT30S")  # 30 seconds
        result = self.resolver.resolve(v, {})
        assert result == 30.0

    def test_duration_iso8601_minutes(self):
        v = make_value("duration", "PT5M")  # 5 minutes
        result = self.resolver.resolve(v, {})
        assert result == 300.0

    def test_none_returns_none(self):
        v = make_value("string", None)
        result = self.resolver.resolve(v, {})
        assert result is None

    def test_resolve_none_value_returns_none(self):
        assert self.resolver.resolve(None, {}) is None


class TestEnvVarProvided:
    def setup_method(self):
        self.store = make_store()
        self.resolver = Resolver(self.store)

    def test_env_var_present(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "secret123")
        v = make_value("provided", {"source": "ENV_VAR", "lookup": "MY_API_KEY"})
        result = self.resolver.resolve(v, {})
        assert result == "secret123"

    def test_env_var_missing_raises(self, monkeypatch):
        monkeypatch.delenv("MY_MISSING_VAR", raising=False)
        v = make_value("provided", {"source": "ENV_VAR", "lookup": "MY_MISSING_VAR"})
        with pytest.raises(QuonfigEnvVarNotSetError):
            self.resolver.resolve(v, {})

    def test_unknown_source_raises(self):
        v = make_value("provided", {"source": "UNKNOWN", "lookup": "foo"})
        with pytest.raises(QuonfigEnvVarNotSetError):
            self.resolver.resolve(v, {})


class TestWeightedValues:
    def setup_method(self):
        self.store = make_store()
        self.resolver = Resolver(self.store)

    def test_single_value_always_selected(self):
        """With only one option, it should always be selected."""
        weighted_val = {
            "weightedValues": [
                {"weight": 100, "value": {"type": "string", "value": "only-option"}}
            ],
            "hashByPropertyName": "user.id",
        }
        v = make_value("weighted_values", weighted_val)
        ctx = {"user": {"id": "any-user"}}
        result = self.resolver.resolve(v, ctx)
        assert result == "only-option"

    def test_weighted_values_deterministic(self):
        """Same user should always get the same value."""
        weighted_val = {
            "weightedValues": [
                {"weight": 50, "value": {"type": "string", "value": "option-a"}},
                {"weight": 50, "value": {"type": "string", "value": "option-b"}},
            ],
            "hashByPropertyName": "user.id",
        }
        v = make_value("weighted_values", weighted_val)
        ctx = {"user": {"id": "fixed-user-123"}}
        result1 = self.resolver.resolve(v, ctx)
        result2 = self.resolver.resolve(v, ctx)
        assert result1 == result2

    def test_different_users_can_get_different_values(self):
        """With even weights, different users should eventually get different values."""
        weighted_val = {
            "weightedValues": [
                {"weight": 50, "value": {"type": "string", "value": "option-a"}},
                {"weight": 50, "value": {"type": "string", "value": "option-b"}},
            ],
            "hashByPropertyName": "user.id",
        }
        v = make_value("weighted_values", weighted_val)
        results = set()
        for i in range(20):
            ctx = {"user": {"id": f"user-{i}"}}
            results.add(self.resolver.resolve(v, ctx))
            if len(results) == 2:
                break
        assert len(results) == 2, "Expected both options to be selected across users"

    def test_empty_weighted_values_returns_none(self):
        weighted_val = {
            "weightedValues": [],
            "hashByPropertyName": "user.id",
        }
        v = make_value("weighted_values", weighted_val)
        result = self.resolver.resolve(v, {"user": {"id": "u1"}})
        assert result is None
