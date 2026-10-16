"""Tests for all operator implementations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quonfig.operators import (
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

    def test_bool_uses_lowercase(self):
        # R1: bool -> "true"/"false", case-sensitive.
        assert op(prop_is_one_of, True, ["true"]) is True
        assert op(prop_is_one_of, False, ["false"]) is True
        assert op(prop_is_one_of, True, ["True"]) is False
        assert op(prop_is_not_one_of, True, ["true"]) is False

    def test_whole_number_floats_use_plain_digits(self):
        # R1: integral numbers stringify as digits whatever the runtime type.
        assert op(prop_is_one_of, 1.0, ["1"]) is True
        assert op(prop_is_one_of, 1234567.0, ["1234567"]) is True
        assert op(prop_is_one_of, 5e9, ["5000000000"]) is True
        assert op(prop_is_one_of, 1.0, ["1.0"]) is False
        assert op(prop_is_one_of, 1.5, ["1.5"]) is True

    def test_list_prop_elements(self):
        assert op(prop_is_one_of, [True, 2.0], ["2"]) is True

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

    def test_non_string_prop_is_stringified(self):
        # R2: string operators stringify non-string context values.
        assert op(prop_starts_with_one_of, 123, ["12"]) is True
        assert op(prop_starts_with_one_of, True, ["tr"]) is True
        assert op(prop_starts_with_one_of, 1234567.0, ["1234567"]) is True

    def test_missing_or_list_prop_does_not_match(self):
        assert op(prop_starts_with_one_of, None, ["N"]) is False
        assert op(prop_starts_with_one_of, ["foo"], ["foo"]) is False


class TestPropEndsWithOneOf:
    def test_match(self):
        assert op(prop_ends_with_one_of, "foobar", ["bar", "abc"]) is True

    def test_no_match(self):
        assert op(prop_ends_with_one_of, "foobar", ["foo", "abc"]) is False

    def test_whole_number_float_prop_uses_plain_digits(self):
        # R1/R2: 1.0 is "1", not "1.0"; 5e9 is "5000000000", not "5e+09".
        assert op(prop_ends_with_one_of, 1.0, ["1"]) is True
        assert op(prop_ends_with_one_of, 1.0, [".0"]) is False
        assert op(prop_ends_with_one_of, 5e9, ["000"]) is True
        assert op(prop_ends_with_one_of, False, ["lse"]) is True


class TestPropContainsOneOf:
    def test_match(self):
        assert op(prop_contains_one_of, "foobar", ["oo", "xyz"]) is True

    def test_no_match(self):
        assert op(prop_contains_one_of, "foobar", ["xyz", "abc"]) is False

    def test_non_string_prop_is_stringified(self):
        assert op(prop_contains_one_of, 123, ["2"]) is True
        assert op(prop_contains_one_of, 1234567.0, ["e+"]) is False


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
        # Context values that are strings are rejected by numeric operators
        # (matches cross-SDK behavior: only actual numbers are compared numerically)
        assert op(prop_less_than, "1.5", "2.0") is False

    def test_non_numeric_returns_false(self):
        assert op(prop_less_than, "abc", 2.0) is False


class TestInIntRange:
    def test_in_range(self):
        result = in_int_range(5, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is True

    def test_at_boundary_start(self):
        result = in_int_range(1, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is True

    def test_at_boundary_end_is_excluded(self):
        # R3: [start, end) -- the end bound is exclusive.
        result = in_int_range(10, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is False

    def test_missing_end_is_unbounded_above(self):
        assert in_int_range(5_000_000_000, {"start": 0}, _EMPTY_CTX, _EMPTY_STORE) is True
        assert in_int_range(-1, {"start": 0}, _EMPTY_CTX, _EMPTY_STORE) is False

    def test_missing_start_is_unbounded_below(self):
        assert in_int_range(-5, {"end": 10}, _EMPTY_CTX, _EMPTY_STORE) is True
        assert in_int_range(10, {"end": 10}, _EMPTY_CTX, _EMPTY_STORE) is False

    def test_digit_string_bounds(self):
        # proto int64 JSON form
        assert in_int_range(5, {"start": "0", "end": "10"}, _EMPTY_CTX, _EMPTY_STORE) is True

    def test_low_high_keys_are_not_bounds(self):
        # low/high is not a supported shape; with neither start nor end the
        # range is unbounded (matches go and the Prefab proto).
        assert in_int_range(50, {"low": 0, "high": 10}, _EMPTY_CTX, _EMPTY_STORE) is True

    def test_whole_number_float_context(self):
        assert in_int_range(9.0, {"start": 0, "end": 10}, _EMPTY_CTX, _EMPTY_STORE) is True
        assert in_int_range(10.0, {"start": 0, "end": 10}, _EMPTY_CTX, _EMPTY_STORE) is False

    def test_out_of_range(self):
        result = in_int_range(11, {"start": 1, "end": 10}, _EMPTY_CTX, _EMPTY_STORE)
        assert result is False

    def test_list_format_is_not_supported(self):
        result = in_int_range(5, [1, 10], _EMPTY_CTX, _EMPTY_STORE)
        assert result is False


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
        assert op(prop_semver_equal, "v1.0.0", "1.0.0") is False
        assert op(prop_semver_equal, "1.0.0\n", "1.0.0") is False
        assert op(prop_semver_equal, "01.0.0", "1.0.0") is False

    def test_prerelease_sorts_before_release(self):
        # R4: SemVer 2.0 precedence.
        assert op(prop_semver_equal, "1.0.0-beta.1", "1.0.0") is False
        assert op(prop_semver_less_than, "1.0.0-beta.1", "1.0.0") is True
        assert op(prop_semver_greater_than, "1.0.0", "1.0.0-rc.1") is True

    def test_prerelease_identifier_precedence(self):
        # semver.org section 11 example ordering
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        for lo, hi in zip(ordered, ordered[1:]):
            assert op(prop_semver_less_than, lo, hi) is True, (lo, hi)
            assert op(prop_semver_greater_than, hi, lo) is True, (lo, hi)
            assert op(prop_semver_equal, lo, hi) is False, (lo, hi)

    def test_build_metadata_is_ignored(self):
        assert op(prop_semver_equal, "2.0.0+build.5", "2.0.0") is True
        assert op(prop_semver_equal, "2.0.0-rc.1+a", "2.0.0-rc.1+b") is True

    def test_abbreviated_versions(self):
        # Q5: "2" = 2.0.0, "2.1" = 2.1.0, on either side.
        assert op(prop_semver_equal, "2", "2.0.0") is True
        assert op(prop_semver_equal, "2.0", "2.0.0") is True
        assert op(prop_semver_equal, "2.0.0", "2.1") is False
        assert op(prop_semver_less_than, "2.0.5", "2.1") is True
        assert op(prop_semver_greater_than, "4.1", "4.0.18") is True
        # an abbreviated version carries no prerelease or build
        assert op(prop_semver_equal, "2-beta", "2.0.0-beta") is False


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

    def test_date_only_and_offsetless_strings_never_match(self):
        # R5: both sides need a number, digit string, or RFC3339 with Z/offset.
        for bad in ("2023-01-01", "2023-01-01T00:00:00", "Jan 1 2023", "2023-01-01 00:00:00Z"):
            assert op(prop_before, bad, self.REFERENCE_MILLIS) is False, bad
            assert op(prop_after, bad, 0) is False, bad
            assert op(prop_before, 0, bad) is False, bad

    def test_digit_string_context(self):
        assert op(prop_before, "1704067200000", self.REFERENCE_MILLIS) is True
        assert op(prop_before, "1704067200000\n", self.REFERENCE_MILLIS) is False

    def test_rfc3339_criterion(self):
        assert op(prop_before, 1704067200000, "2024-01-01T12:00:00Z") is True
        assert op(prop_before, "2025-01-01T00:00:00Z", "2024-01-01T12:00:00Z") is False
        assert op(prop_after, 1704111000000, "2024-01-01T13:00:00+02:00") is True

    def test_digit_string_criterion(self):
        assert op(prop_after, 1000, "999") is True
        assert op(prop_after, 500, "999") is False

    def test_fractional_seconds(self):
        assert op(prop_after, "2024-01-01T12:00:00.001Z", self.REFERENCE_MILLIS) is True
        assert op(prop_after, "2024-01-01T12:00:00.000123456Z", self.REFERENCE_MILLIS) is False

    def test_float_and_bool_values(self):
        # a float epoch ms is truncated; a bool is not a timestamp
        assert op(prop_before, float(self.REFERENCE_MILLIS) - 0.5, self.REFERENCE_MILLIS) is True
        assert op(prop_after, 5e12, self.REFERENCE_MILLIS) is True
        assert op(prop_before, True, self.REFERENCE_MILLIS) is False

    def test_naive_datetime_keeps_local_time_behavior(self):
        # datetime objects are outside R5 and keep today's handling.
        naive = datetime(2023, 6, 1, 0, 0, 0)
        assert op(prop_before, naive, int(naive.timestamp() * 1000) + 1) is True

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
        assert op(hierarchical_match, 123, "12") is True


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
