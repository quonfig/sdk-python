"""Tests for all operator implementations."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from quonfig.operators import (
    OPERATOR_DISPATCH,
    always_true,
    evaluate_operator,
    hierarchical_match,
    in_int_range,
    not_set,
    prop_after,
    prop_before,
    prop_contains_one_of,
    prop_does_not_match,
    prop_ends_with_one_of,
    prop_greater_than,
    prop_greater_than_or_equal,
    prop_is_not_one_of,
    prop_is_one_of,
    prop_less_than,
    prop_less_than_or_equal,
    prop_matches,
    prop_semver_equal,
    prop_semver_greater_than,
    prop_semver_less_than,
    prop_starts_with_one_of,
)
from quonfig.store import ConfigStore

# Shared empty store/contexts for tests that don't need them
_EMPTY_STORE = ConfigStore()
_EMPTY_CTX = {}


def op(fn, prop_val, criterion_val):
    return fn(prop_val, criterion_val, _EMPTY_CTX, _EMPTY_STORE)


class TestAlwaysTrue:
    def test_always_returns_true(self):
        assert op(always_true, None, None) is True
        assert op(always_true, "anything", "anything") is True


class TestNotSet:
    def test_none_is_not_set(self):
        assert op(not_set, None, None) is True

    def test_value_is_set(self):
        assert op(not_set, "value", None) is False

    def test_empty_string_is_set(self):
        assert op(not_set, "", None) is False


class TestPropIsOneOf:
    def test_string_match(self):
        assert op(prop_is_one_of, "alice", ["alice", "bob"]) is True

    def test_string_no_match(self):
        assert op(prop_is_one_of, "charlie", ["alice", "bob"]) is False

    def test_int_as_string_match(self):
        assert op(prop_is_one_of, 42, ["42", "100"]) is True

    def test_single_value_criterion(self):
        assert op(prop_is_one_of, "alice", "alice") is True

    def test_none_prop_does_not_match(self):
        assert op(prop_is_one_of, None, ["alice"]) is False


class TestPropIsNotOneOf:
    def test_not_in_list(self):
        assert op(prop_is_not_one_of, "charlie", ["alice", "bob"]) is True

    def test_in_list(self):
        assert op(prop_is_not_one_of, "alice", ["alice", "bob"]) is False


class TestPropStartsWithOneOf:
    def test_match(self):
        assert op(prop_starts_with_one_of, "foobar", ["foo", "abc"]) is True

    def test_no_match(self):
        assert op(prop_starts_with_one_of, "foobar", ["bar", "abc"]) is False

    def test_non_string_prop(self):
        assert op(prop_starts_with_one_of, 123, ["12"]) is False


class TestPropEndsWithOneOf:
    def test_match(self):
        assert op(prop_ends_with_one_of, "foobar", ["bar", "abc"]) is True

    def test_no_match(self):
        assert op(prop_ends_with_one_of, "foobar", ["foo", "abc"]) is False


class TestPropContainsOneOf:
    def test_match(self):
        assert op(prop_contains_one_of, "foobar", ["oo", "xyz"]) is True

    def test_no_match(self):
        assert op(prop_contains_one_of, "foobar", ["xyz", "abc"]) is False

    def test_non_string_prop(self):
        assert op(prop_contains_one_of, 123, ["1"]) is False


class TestPropMatches:
    def test_simple_match(self):
        assert op(prop_matches, "hello world", "world") is True

    def test_no_match(self):
        assert op(prop_matches, "hello world", "universe") is False

    def test_regex_pattern(self):
        assert op(prop_matches, "hello123", r"\d+") is True

    def test_invalid_pattern(self):
        assert op(prop_matches, "hello", "[invalid") is False

    def test_non_string_prop(self):
        assert op(prop_matches, 123, r"\d+") is False

    def test_anchor_match(self):
        assert op(prop_matches, "hello world", "^world") is False
        assert op(prop_matches, "hello world", "^hello") is True


class TestPropDoesNotMatch:
    def test_no_match_returns_true(self):
        assert op(prop_does_not_match, "hello world", "universe") is True

    def test_match_returns_false(self):
        assert op(prop_does_not_match, "hello world", "world") is False


class TestNumericOperators:
    def test_less_than_true(self):
        assert op(prop_less_than, 1.5, 2.0) is True

    def test_less_than_equal_is_false(self):
        assert op(prop_less_than, 2.0, 2.0) is False

    def test_greater_than_true(self):
        assert op(prop_greater_than, 3.0, 2.0) is True

    def test_greater_than_equal_is_false(self):
        assert op(prop_greater_than, 2.0, 2.0) is False

    def test_less_than_or_equal_equal(self):
        assert op(prop_less_than_or_equal, 2.0, 2.0) is True

    def test_less_than_or_equal_less(self):
        assert op(prop_less_than_or_equal, 1.0, 2.0) is True

    def test_less_than_or_equal_greater(self):
        assert op(prop_less_than_or_equal, 3.0, 2.0) is False

    def test_greater_than_or_equal_equal(self):
        assert op(prop_greater_than_or_equal, 2.0, 2.0) is True

    def test_numeric_coercion_string(self):
        assert op(prop_less_than, "1.5", "2.0") is True

    def test_non_numeric_returns_false(self):
        assert op(prop_less_than, "abc", 2.0) is False


class TestInIntRange:
    def test_in_range(self):
        result = in_int_range(5, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is True

    def test_at_boundary_start(self):
        result = in_int_range(1, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is True

    def test_at_boundary_end(self):
        result = in_int_range(10, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is True

    def test_out_of_range(self):
        result = in_int_range(11, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is False

    def test_list_format(self):
        result = in_int_range(5, [1, 10], _EMPTY_CTX, _EMPTY_STORE)
        assert result is True


class TestSemverOperators:
    def test_semver_equal(self):
        assert op(prop_semver_equal, "1.2.3", "1.2.3") is True

    def test_semver_not_equal(self):
        assert op(prop_semver_equal, "1.2.3", "1.2.4") is False

    def test_semver_less_than(self):
        assert op(prop_semver_less_than, "1.9.9", "2.0.0") is True

    def test_semver_greater_than(self):
        assert op(prop_semver_greater_than, "2.0.0", "1.9.9") is True

    def test_invalid_version_returns_false(self):
        assert op(prop_semver_equal, "not.a.version", "1.0.0") is False
        assert op(prop_semver_equal, "1.0.0", "not.a.version") is False


class TestDateOperators:
    REFERENCE_TIME = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    REFERENCE_MILLIS = int(REFERENCE_TIME.timestamp() * 1000)

    def test_before_rfc3339_true(self):
        assert op(prop_before, "2023-12-31T12:00:00Z", self.REFERENCE_MILLIS) is True

    def test_before_rfc3339_false(self):
        assert op(prop_before, "2024-01-02T12:00:00Z", self.REFERENCE_MILLIS) is False

    def test_after_rfc3339_true(self):
        assert op(prop_after, "2024-01-02T12:00:00Z", self.REFERENCE_MILLIS) is True

    def test_after_millis_true(self):
        future = self.REFERENCE_MILLIS + 3600 * 1000
        assert op(prop_after, future, self.REFERENCE_MILLIS) is True

    def test_before_datetime_obj(self):
        past = self.REFERENCE_TIME - timedelta(days=1)
        assert op(prop_before, past, self.REFERENCE_MILLIS) is True

    def test_invalid_date_string(self):
        assert op(prop_before, "not-a-date", self.REFERENCE_MILLIS) is False

    def test_timezone_offset_rfc3339(self):
        # 2023-12-31T14:00:00+02:00 is the same as 2023-12-31T12:00:00Z, which is before reference
        assert op(prop_before, "2023-12-31T14:00:00+02:00", self.REFERENCE_MILLIS) is True


class TestHierarchicalMatch:
    def test_prefix_match(self):
        assert op(hierarchical_match, "log-levels.app.auth", "log-levels.app") is True

    def test_exact_match(self):
        assert op(hierarchical_match, "log-levels.app", "log-levels.app") is True

    def test_no_match(self):
        assert op(hierarchical_match, "log-levels.other", "log-levels.app") is False

    def test_non_string_prop(self):
        assert op(hierarchical_match, 123, "log-levels") is False


class TestEvaluateOperatorDispatch:
    def test_unknown_operator_returns_false(self):
        result = evaluate_operator("UNKNOWN_OP", "val", "val", _EMPTY_CTX, _EMPTY_STORE)
        assert result is False

    def test_always_true_dispatches(self):
        result = evaluate_operator("ALWAYS_TRUE", None, None, _EMPTY_CTX, _EMPTY_STORE)
        assert result is True

    def test_does_not_start_with(self):
        result = evaluate_operator(
            "PROP_DOES_NOT_START_WITH_ONE_OF", "foobar", ["foo"], _EMPTY_CTX, _EMPTY_STORE
        )
        assert result is False

    def test_does_not_end_with(self):
        result = evaluate_operator(
            "PROP_DOES_NOT_END_WITH_ONE_OF", "foobar", ["baz"], _EMPTY_CTX, _EMPTY_STORE
        )
        assert result is True

    def test_does_not_contain(self):
        result = evaluate_operator(
            "PROP_DOES_NOT_CONTAIN_ONE_OF", "foobar", ["xyz"], _EMPTY_CTX, _EMPTY_STORE
        )
        assert result is True
