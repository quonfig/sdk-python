# AUTO-GENERATED from integration-test-data/tests/eval/enabled.yaml. DO NOT EDIT.
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


# returns the correct value for a simple flag
def test_returns_the_correct_value_for_a_simple_flag(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.simple")
    assert result is True


# always returns false for a non-boolean flag
def test_always_returns_false_for_a_non_boolean_flag(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.integer")
    assert result is False


# returns false for a flag key that does not exist
def test_returns_false_for_a_flag_key_that_does_not_exist(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("my-missing-key")
    assert result is False


# returns false for a flag key that does not exist with a context
def test_returns_false_for_a_flag_key_that_does_not_exist_with_a_context(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "my-missing-key", contexts={"user": {"key": "michael", "email": "michael@example.com"}}
    )
    assert result is False


# returns true for a PROP_IS_ONE_OF rule when any prop matches
def test_returns_true_for_a_prop_is_one_of_rule_when_any_prop_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.positive",
        contexts={"": {"name": "michael", "domain": "something.com"}},
    )
    assert result is True


# returns false for a PROP_IS_ONE_OF rule when no prop matches
def test_returns_false_for_a_prop_is_one_of_rule_when_no_prop_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.positive",
        contexts={"": {"name": "lauren", "domain": "something.com"}},
    )
    assert result is False


# returns true for a PROP_IS_NOT_ONE_OF rule when any prop doesn't match
def test_returns_true_for_a_prop_is_not_one_of_rule_when_any_prop_doesn_t_match(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.negative",
        contexts={"": {"name": "lauren", "domain": "prefab.cloud"}},
    )
    assert result is True


# returns false for a PROP_IS_NOT_ONE_OF rule when all props match
def test_returns_false_for_a_prop_is_not_one_of_rule_when_all_props_match(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.negative",
        contexts={"": {"name": "michael", "domain": "prefab.cloud"}},
    )
    assert result is False


# returns true for PROP_ENDS_WITH_ONE_OF rule when the given prop has a matching suffix
def test_returns_true_for_prop_ends_with_one_of_rule_when_the_given_prop_has_a_matching_suffix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.positive", contexts={"": {"email": "jeff@prefab.cloud"}}
    )
    assert result is True


# returns false for PROP_ENDS_WITH_ONE_OF rule when the given prop doesn't have a matching suffix
def test_returns_false_for_prop_ends_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_suffix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.positive", contexts={"": {"email": "jeff@test.com"}}
    )
    assert result is False


# returns true for PROP_DOES_NOT_END_WITH_ONE_OF rule when the given prop doesn't have a matching suffix
def test_returns_true_for_prop_does_not_end_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_suffix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.negative", contexts={"": {"email": "michael@test.com"}}
    )
    assert result is True


# returns false for PROP_DOES_NOT_END_WITH_ONE_OF rule when the given prop has a matching suffix
def test_returns_false_for_prop_does_not_end_with_one_of_rule_when_the_given_prop_has_a_matching_suffix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.negative", contexts={"": {"email": "michael@prefab.cloud"}}
    )
    assert result is False


# returns true for PROP_STARTS_WITH_ONE_OF rule when the given prop has a matching prefix
def test_returns_true_for_prop_starts_with_one_of_rule_when_the_given_prop_has_a_matching_prefix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.positive", contexts={"user": {"email": "foo@prefab.cloud"}}
    )
    assert result is True


# returns false for PROP_STARTS_WITH_ONE_OF rule when the given prop doesn't have a matching prefix
def test_returns_false_for_prop_starts_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_prefix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.positive",
        contexts={"user": {"email": "notfoo@prefab.cloud"}},
    )
    assert result is False


# returns true for PROP_DOES_NOT_START_WITH_ONE_OF rule when the given prop doesn't have a matching prefix
def test_returns_true_for_prop_does_not_start_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_prefix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.negative",
        contexts={"user": {"email": "notfoo@prefab.cloud"}},
    )
    assert result is True


# returns false for PROP_DOES_NOT_START_WITH_ONE_OF rule when the given prop has a matching prefix
def test_returns_false_for_prop_does_not_start_with_one_of_rule_when_the_given_prop_has_a_matching_prefix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.negative", contexts={"user": {"email": "foo@prefab.cloud"}}
    )
    assert result is False


# returns true for PROP_CONTAINS_ONE_OF rule when the given prop has a matching substring
def test_returns_true_for_prop_contains_one_of_rule_when_the_given_prop_has_a_matching_substring(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.positive",
        contexts={"user": {"email": "somefoo@prefab.cloud"}},
    )
    assert result is True


# returns false for PROP_CONTAINS_ONE_OF rule when the given prop doesn't have a matching substring
def test_returns_false_for_prop_contains_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_substring(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.positive", contexts={"user": {"email": "info@prefab.cloud"}}
    )
    assert result is False


# returns true for PROP_DOES_NOT_CONTAIN_ONE_OF rule when the given prop doesn't have a matching substring
def test_returns_true_for_prop_does_not_contain_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_substring(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.negative", contexts={"user": {"email": "info@prefab.cloud"}}
    )
    assert result is True


# returns false for PROP_DOES_NOT_CONTAIN_ONE_OF rule when the given prop has a matching substring
def test_returns_false_for_prop_does_not_contain_one_of_rule_when_the_given_prop_has_a_matching_substring(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.negative", contexts={"user": {"email": "notfoo@prefab.cloud"}}
    )
    assert result is False


# returns true for IN_SEG when the segment rule matches
def test_returns_true_for_in_seg_when_the_segment_rule_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.positive", contexts={"user": {"key": "lauren"}}
    )
    assert result is True


# returns false for IN_SEG when the segment rule doesn't match
def test_returns_false_for_in_seg_when_the_segment_rule_doesn_t_match(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.positive", contexts={"user": {"key": "josh"}}
    )
    assert result is False


# returns false for IN_SEG if any segment rule fails to match
def test_returns_false_for_in_seg_if_any_segment_rule_fails_to_match(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is False


# returns true for IN_SEG (segment-and) if all rules matches
def test_returns_true_for_in_seg_segment_and_if_all_rules_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"user": {"key": "michael"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is True


# returns true for IN_SEG (segment-or) if any segment rule matches (lookup)
def test_returns_true_for_in_seg_segment_or_if_any_segment_rule_matches_lookup(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-or",
        contexts={"user": {"key": "michael"}, "": {"domain": "example.com"}},
    )
    assert result is True


# returns true for IN_SEG (segment-or) if any segment rule matches (prop)
def test_returns_true_for_in_seg_segment_or_if_any_segment_rule_matches_prop(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-or",
        contexts={"user": {"key": "nobody"}, "": {"domain": "gmail.com"}},
    )
    assert result is True


# returns true for NOT_IN_SEG when the segment rule doesn't match
def test_returns_true_for_not_in_seg_when_the_segment_rule_doesn_t_match(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.negative", contexts={"user": {"key": "josh"}}
    )
    assert result is True


# returns false for NOT_IN_SEG when the segment rule matches
def test_returns_false_for_not_in_seg_when_the_segment_rule_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.negative", contexts={"user": {"key": "michael"}}
    )
    assert result is False


# returns false for NOT_IN_SEG if any segment rule matches
def test_returns_false_for_not_in_seg_if_any_segment_rule_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.multiple-criteria.negative",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is True


# returns true for NOT_IN_SEG if no segment rule matches
def test_returns_true_for_not_in_seg_if_no_segment_rule_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.multiple-criteria.negative",
        contexts={"user": {"key": "josh"}, "": {"domain": "something.com"}},
    )
    assert result is True


# returns true for NOT_IN_SEG (segment-and) if not segment rule fails to match
def test_returns_true_for_not_in_seg_segment_and_if_not_segment_rule_fails_to_match(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-and",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is True


# returns true for IN_SEG (segment-and) if not segment rule fails to match
def test_returns_true_for_in_seg_segment_and_if_not_segment_rule_fails_to_match(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is False


# returns false for NOT_IN_SEG (segment-and) if segment rules matches
def test_returns_false_for_not_in_seg_segment_and_if_segment_rules_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-and",
        contexts={"user": {"key": "michael"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is False


# returns true for NOT_IN_SEG (segment-or) if no segment rule matches
def test_returns_true_for_not_in_seg_segment_or_if_no_segment_rule_matches(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-or",
        contexts={"user": {"key": "nobody"}, "": {"domain": "example.com"}},
    )
    assert result is True


# returns false for NOT_IN_SEG (segment-or) if one segment rule matches (prop)
def test_returns_false_for_not_in_seg_segment_or_if_one_segment_rule_matches_prop(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-or",
        contexts={"user": {"key": "nobody"}, "": {"domain": "gmail.com"}},
    )
    assert result is False


# returns false for NOT_IN_SEG (segment-or) if one segment rule matches (lookup)
def test_returns_false_for_not_in_seg_segment_or_if_one_segment_rule_matches_lookup(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-or",
        contexts={"user": {"key": "michael"}, "": {"domain": "example.com"}},
    )
    assert result is False


# returns true for PROP_BEFORE rule when the given prop represents a date (string) before the rule's time
def test_returns_true_for_prop_before_rule_when_the_given_prop_represents_a_date_string_before_the_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "2024-11-01T00:00:00Z"}}
    )
    assert result is True


# returns true for PROP_BEFORE rule when the given prop represents a date (number) before the rule's time
def test_returns_true_for_prop_before_rule_when_the_given_prop_represents_a_date_number_before_the_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": 1730419200000}}
    )
    assert result is True


# returns false for PROP_BEFORE rule when the given prop represents a date (number) exactly matching rule's time
def test_returns_false_for_prop_before_rule_when_the_given_prop_represents_a_date_number_exactly_matching_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": 1733011200000}}
    )
    assert result is False


# returns false for PROP_BEFORE rule when the given prop represents a date (number) AFTER the rule's time
def test_returns_false_for_prop_before_rule_when_the_given_prop_represents_a_date_number_after_the_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "2025-01-01T00:00:00Z"}}
    )
    assert result is False


# returns false for PROP_BEFORE rule when the given prop won't parse as a date
def test_returns_false_for_prop_before_rule_when_the_given_prop_won_t_parse_as_a_date(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "not a date"}}
    )
    assert result is False


# returns false for PROP_BEFORE rule using current-time relative to 2050-01-01
def test_returns_false_for_prop_before_rule_using_current_time_relative_to_2050_01_01(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.before.current-time")
    assert result is True


# returns true for PROP_AFTER rule when the given prop represents a date (string) after the rule's time
def test_returns_true_for_prop_after_rule_when_the_given_prop_represents_a_date_string_after_the_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": "2025-01-01T00:00:00Z"}}
    )
    assert result is True


# returns true for PROP_AFTER rule when the given prop represents a date (number) after the rule's time
def test_returns_true_for_prop_after_rule_when_the_given_prop_represents_a_date_number_after_the_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": 1735689600000}}
    )
    assert result is True


# returns false for PROP_AFTER rule when the given prop represents a date (number) exactly matching rule's time
def test_returns_false_for_prop_after_rule_when_the_given_prop_represents_a_date_number_exactly_matching_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": 1733011200000}}
    )
    assert result is False


# returns false for PROP_BEFORE rule when the given prop represents a date (number) BEFORE the rule's time
def test_returns_false_for_prop_before_rule_when_the_given_prop_represents_a_date_number_before_the_rule_s_time(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": "2024-01-01T00:00:00Z"}}
    )
    assert result is False


# returns false for PROP_AFTER rule when the given prop won't parse as a date
def test_returns_false_for_prop_after_rule_when_the_given_prop_won_t_parse_as_a_date(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": "not a date"}}
    )
    assert result is False


# returns false for PROP_AFTER rule using current-time relative to 2025-01-01
def test_returns_false_for_prop_after_rule_using_current_time_relative_to_2025_01_01(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.after.current-time")
    assert result is True


# returns true for PROP_LESS_THAN rule when the given prop is less than the rule's value
def test_returns_true_for_prop_less_than_rule_when_the_given_prop_is_less_than_the_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": 20}})
    assert result is True


# returns true for PROP_LESS_THAN rule when the given prop is less than the rule's value (float)
def test_returns_true_for_prop_less_than_rule_when_the_given_prop_is_less_than_the_rule_s_value_float(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": 20.5}})
    assert result is True


# returns false for PROP_LESS_THAN rule when the given prop is equal to rule's value
def test_returns_false_for_prop_less_than_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": 30}})
    assert result is False


# returns false for PROP_LESS_THAN rule when the given prop a string
def test_returns_false_for_prop_less_than_rule_when_the_given_prop_a_string(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": "20"}})
    assert result is False


# returns true for PROP_LESS_THAN_OR_EQUAL rule when the given prop is less than the rule's value
def test_returns_true_for_prop_less_than_or_equal_rule_when_the_given_prop_is_less_than_the_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than-or-equal", contexts={"user": {"age": 20}})
    assert result is True


# returns true for PROP_LESS_THAN_OR_EQUAL rule when the given prop is less than the rule's value (float)
def test_returns_true_for_prop_less_than_or_equal_rule_when_the_given_prop_is_less_than_the_rule_s_value_float(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.less-than-or-equal", contexts={"user": {"age": 20.5}}
    )
    assert result is True


# returns false for PROP_LESS_THAN_OR_EQUAL rule when the given prop is equal to rule's value
def test_returns_false_for_prop_less_than_or_equal_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than-or-equal", contexts={"user": {"age": 30}})
    assert result is True


# returns false for PROP_LESS_THAN_OR_EQUAL rule when the given prop a string
def test_returns_false_for_prop_less_than_or_equal_rule_when_the_given_prop_a_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.less-than-or-equal", contexts={"user": {"age": "20"}}
    )
    assert result is False


# returns true for PROP_GREATER_THAN rule when the given prop is greater than the rule's value
def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": 100}})
    assert result is True


# returns true for PROP_GREATER_THAN rule when the given prop is greater than the rule's value (float)
def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_value_float(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": 30.5}})
    assert result is True


# returns true for PROP_GREATER_THAN rule when the given prop is greater than the rule's float value (float)
def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_float_value_float(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than.double", contexts={"user": {"age": 32.7}}
    )
    assert result is True


# returns true for PROP_GREATER_THAN rule when the given prop is greater than the rule's float value (integer)
def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_float_value_integer(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than.double", contexts={"user": {"age": 32}}
    )
    assert result is True


# returns false for PROP_GREATER_THAN rule when the given prop is equal to rule's value
def test_returns_false_for_prop_greater_than_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": 30}})
    assert result is False


# returns false for PROP_GREATER_THAN rule when the given prop a string
def test_returns_false_for_prop_greater_than_rule_when_the_given_prop_a_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": "100"}})
    assert result is False


# returns true for PROP_GREATER_THAN_OR_EQUAL rule when the given prop is greater than the rule's value
def test_returns_true_for_prop_greater_than_or_equal_rule_when_the_given_prop_is_greater_than_the_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": 30}}
    )
    assert result is True


# returns true for PROP_GREATER_THAN_OR_EQUAL rule when the given prop is greater than the rule's value (float)
def test_returns_true_for_prop_greater_than_or_equal_rule_when_the_given_prop_is_greater_than_the_rule_s_value_float(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": 30.5}}
    )
    assert result is True


# returns true for PROP_GREATER_THAN_OR_EQUAL rule when the given prop is equal to rule's value
def test_returns_true_for_prop_greater_than_or_equal_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": 30}}
    )
    assert result is True


# returns false for PROP_GREATER_THAN_OR_EQUAL rule when the given prop a string
def test_returns_false_for_prop_greater_than_or_equal_rule_when_the_given_prop_a_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": "100"}}
    )
    assert result is False


# returns true for PROP_MATCHES rule when the given prop matches the regex
def test_returns_true_for_prop_matches_rule_when_the_given_prop_matches_the_regex(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.matches", contexts={"user": {"code": "aaaaaab"}})
    assert result is True


# returns false for PROP_MATCHES rule when the given prop does not match the regex
def test_returns_false_for_prop_matches_rule_when_the_given_prop_does_not_match_the_regex(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.matches", contexts={"user": {"code": "aa"}})
    assert result is False


# returns true for PROP_DOES_NOT_MATCH rule when the given prop does not match the regex
def test_returns_true_for_prop_does_not_match_rule_when_the_given_prop_does_not_match_the_regex(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.does-not-match", contexts={"user": {"code": "b"}})
    assert result is True


# returns false for PROP_DOES_NOT_MATCH rule when the given prop matches the regex
def test_returns_false_for_prop_does_not_match_rule_when_the_given_prop_matches_the_regex(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match", contexts={"user": {"code": "aabb"}}
    )
    assert result is False


# returns true for PROP_MATCHES when the regex matches part of the prop (unanchored search)
def test_returns_true_for_prop_matches_when_the_regex_matches_part_of_the_prop_unanchored_search(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.matches", contexts={"user": {"code": "xaab"}})
    assert result is True


# returns false for PROP_DOES_NOT_MATCH when the regex matches part of the prop (unanchored search)
def test_returns_false_for_prop_does_not_match_when_the_regex_matches_part_of_the_prop_unanchored_search(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match", contexts={"user": {"code": "xaab"}}
    )
    assert result is False


# returns true for PROP_MATCHES with an anchored regex when the whole prop matches
def test_returns_true_for_prop_matches_with_an_anchored_regex_when_the_whole_prop_matches(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.matches.anchored", contexts={"user": {"code": "123"}}
    )
    assert result is True


# returns false for PROP_MATCHES with an anchored regex when the prop is too long
def test_returns_false_for_prop_matches_with_an_anchored_regex_when_the_prop_is_too_long(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.matches.anchored", contexts={"user": {"code": "1234"}}
    )
    assert result is False


# returns false for PROP_MATCHES with an anchored regex when the prop has a non-matching character
def test_returns_false_for_prop_matches_with_an_anchored_regex_when_the_prop_has_a_non_matching_character(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.matches.anchored", contexts={"user": {"code": "12a"}}
    )
    assert result is False


# returns false for PROP_MATCHES when the prop is not a string
def test_returns_false_for_prop_matches_when_the_prop_is_not_a_string(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.matches.anchored", contexts={"user": {"code": 123}})
    assert result is False


# returns false for PROP_MATCHES when the prop is missing
def test_returns_false_for_prop_matches_when_the_prop_is_missing(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.matches.anchored", contexts={"user": {"key": "michael"}}
    )
    assert result is False


# returns true for PROP_DOES_NOT_MATCH with an anchored regex when the prop is too long
def test_returns_true_for_prop_does_not_match_with_an_anchored_regex_when_the_prop_is_too_long(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match.anchored", contexts={"user": {"code": "1234"}}
    )
    assert result is True


# returns true for PROP_DOES_NOT_MATCH with an anchored regex when the prop has a non-matching character
def test_returns_true_for_prop_does_not_match_with_an_anchored_regex_when_the_prop_has_a_non_matching_character(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match.anchored", contexts={"user": {"code": "12a"}}
    )
    assert result is True


# returns false for PROP_DOES_NOT_MATCH with an anchored regex when the whole prop matches
def test_returns_false_for_prop_does_not_match_with_an_anchored_regex_when_the_whole_prop_matches(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match.anchored", contexts={"user": {"code": "123"}}
    )
    assert result is False


# returns false for PROP_DOES_NOT_MATCH when the prop is not a string
def test_returns_false_for_prop_does_not_match_when_the_prop_is_not_a_string(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match.anchored", contexts={"user": {"code": 123}}
    )
    assert result is False


# returns false for PROP_DOES_NOT_MATCH when the prop is missing
def test_returns_false_for_prop_does_not_match_when_the_prop_is_missing(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match.anchored", contexts={"user": {"key": "michael"}}
    )
    assert result is False


# returns false for PROP_MATCHES when the stored regex is invalid
def test_returns_false_for_prop_matches_when_the_stored_regex_is_invalid(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.matches.invalid-pattern", contexts={"user": {"code": "("}}
    )
    assert result is False


# returns false for PROP_DOES_NOT_MATCH when the stored regex is invalid
def test_returns_false_for_prop_does_not_match_when_the_stored_regex_is_invalid(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match.invalid-pattern", contexts={"user": {"code": "abc"}}
    )
    assert result is False


# returns true for IN_SEG when the segment's PROP_DOES_NOT_MATCH criterion holds
def test_returns_true_for_in_seg_when_the_segment_s_prop_does_not_match_criterion_holds(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.does-not-match", contexts={"user": {"code": "abc"}}
    )
    assert result is True


# returns false for IN_SEG when the segment's PROP_DOES_NOT_MATCH regex matches
def test_returns_false_for_in_seg_when_the_segment_s_prop_does_not_match_regex_matches(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.does-not-match", contexts={"user": {"code": "123"}}
    )
    assert result is False


# returns false for IN_SEG when the segment's PROP_DOES_NOT_MATCH prop is missing
def test_returns_false_for_in_seg_when_the_segment_s_prop_does_not_match_prop_is_missing(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.does-not-match", contexts={"user": {"key": "michael"}}
    )
    assert result is False


# returns true for IS_PRESENT rule when the given prop is a non-empty string
def test_returns_true_for_is_present_rule_when_the_given_prop_is_a_non_empty_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-present", contexts={"user": {"id": "abc"}})
    assert result is True


# returns true for IS_PRESENT rule when the given prop is an empty string
def test_returns_true_for_is_present_rule_when_the_given_prop_is_an_empty_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-present", contexts={"user": {"id": ""}})
    assert result is True


# returns true for IS_PRESENT rule when the given prop is the integer zero
def test_returns_true_for_is_present_rule_when_the_given_prop_is_the_integer_zero(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-present", contexts={"user": {"id": 0}})
    assert result is True


# returns true for IS_PRESENT rule when the given prop is boolean false
def test_returns_true_for_is_present_rule_when_the_given_prop_is_boolean_false(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-present", contexts={"user": {"id": False}})
    assert result is True


# returns false for IS_PRESENT rule when the given prop is null
def test_returns_false_for_is_present_rule_when_the_given_prop_is_null(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-present", contexts={"user": {"id": None}})
    assert result is False


# returns false for IS_PRESENT rule when the given prop key is missing from the context
def test_returns_false_for_is_present_rule_when_the_given_prop_key_is_missing_from_the_context(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-present", contexts={"user": {"name": "bob"}})
    assert result is False


# returns false for IS_PRESENT rule when no contexts are provided at all
def test_returns_false_for_is_present_rule_when_no_contexts_are_provided_at_all(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-present")
    assert result is False


# returns false for IS_NOT_PRESENT rule when the given prop is a non-empty string
def test_returns_false_for_is_not_present_rule_when_the_given_prop_is_a_non_empty_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-not-present", contexts={"user": {"id": "abc"}})
    assert result is False


# returns true for IS_NOT_PRESENT rule when the given prop is null
def test_returns_true_for_is_not_present_rule_when_the_given_prop_is_null(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-not-present", contexts={"user": {"id": None}})
    assert result is True


# returns true for IS_NOT_PRESENT rule when the given prop key is missing from the context
def test_returns_true_for_is_not_present_rule_when_the_given_prop_key_is_missing_from_the_context(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-not-present", contexts={"user": {"name": "bob"}})
    assert result is True


# returns true for IS_PRESENT rule on a nested path when the nested prop is set
def test_returns_true_for_is_present_rule_on_a_nested_path_when_the_nested_prop_is_set(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.is-present-nested", contexts={"organization": {"domain": "example.com"}}
    )
    assert result is True


# returns false for IS_PRESENT rule on a nested path when the nested key is missing but the parent context exists
def test_returns_false_for_is_present_rule_on_a_nested_path_when_the_nested_key_is_missing_but_the_parent_context_exists(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.is-present-nested", contexts={"organization": {"name": "Acme Inc"}}
    )
    assert result is False


# returns false for IS_PRESENT rule on a nested path when the parent context is entirely absent
def test_returns_false_for_is_present_rule_on_a_nested_path_when_the_parent_context_is_entirely_absent(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.is-present-nested", contexts={"user": {"id": "abc"}}
    )
    assert result is False


# returns true for PROP_SEMVER_EQUAL rule when the given prop equals the version
def test_returns_true_for_prop_semver_equal_rule_when_the_given_prop_equals_the_version(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-equal", contexts={"app": {"version": "2.0.0"}}
    )
    assert result is True


# returns false for PROP_SEMVER_EQUAL rule when the given prop does not equal the version
def test_returns_false_for_prop_semver_equal_rule_when_the_given_prop_does_not_equal_the_version(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-equal", contexts={"app": {"version": "2.0.1"}}
    )
    assert result is False


# returns true for PROP_SEMVER_EQUAL rule when the given prop is an abbreviated major.minor version
def test_returns_true_for_prop_semver_equal_rule_when_the_given_prop_is_an_abbreviated_major_minor_version(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.semver-equal", contexts={"app": {"version": "2.0"}})
    assert result is True


# returns true for PROP_SEMVER_LESS_THAN rule when the given prop is less than 2.0.0
def test_returns_true_for_prop_semver_less_than_rule_when_the_given_prop_is_less_than_2_0_0(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than", contexts={"app": {"version": "1.5.1"}}
    )
    assert result is True


# returns false for PROP_SEMVER_LESS_THAN rule when the given prop equals the version
def test_returns_false_for_prop_semver_less_than_rule_when_the_given_prop_equals_the_version(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than", contexts={"app": {"version": "2.0.0"}}
    )
    assert result is False


# returns false for PROP_SEMVER_LESS_THAN rule when the given prop is greater than the version
def test_returns_false_for_prop_semver_less_than_rule_when_the_given_prop_is_greater_than_the_version(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than", contexts={"app": {"version": "2.2.1"}}
    )
    assert result is False


# returns true for PROP_SEMVER_GREATER_THAN rule when the given prop is greater than 2.0.0
def test_returns_true_for_prop_semver_greater_than_rule_when_the_given_prop_is_greater_than_2_0_0(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-greater-than", contexts={"app": {"version": "2.5.1"}}
    )
    assert result is True


# returns false for PROP_SEMVER_GREATER_THAN rule when the given prop equals the version
def test_returns_false_for_prop_semver_greater_than_rule_when_the_given_prop_equals_the_version(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-greater-than", contexts={"app": {"version": "2.0.0"}}
    )
    assert result is False


# returns false for PROP_SEMVER_EQUAL rule when the given prop is less than the version
def test_returns_false_for_prop_semver_equal_rule_when_the_given_prop_is_less_than_the_version(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-greater-than", contexts={"app": {"version": "0.0.5"}}
    )
    assert result is False


# returns true for PROP_IS_ONE_OF ["true"] when the context value is boolean true
def test_returns_true_for_prop_is_one_of_true_when_the_context_value_is_boolean_true(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-one-of.bool", contexts={"user": {"beta": True}})
    assert result is True


# returns false for PROP_IS_ONE_OF ["true"] when the context value is boolean false
def test_returns_false_for_prop_is_one_of_true_when_the_context_value_is_boolean_false(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-one-of.bool", contexts={"user": {"beta": False}})
    assert result is False


# returns true for PROP_IS_ONE_OF ["true"] when the context value is the string "true"
def test_returns_true_for_prop_is_one_of_true_when_the_context_value_is_the_string_true(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.is-one-of.bool", contexts={"user": {"beta": "true"}}
    )
    assert result is True


# returns false for PROP_IS_NOT_ONE_OF ["true"] when the context value is boolean true
def test_returns_false_for_prop_is_not_one_of_true_when_the_context_value_is_boolean_true(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.is-not-one-of.bool", contexts={"user": {"beta": True}}
    )
    assert result is False


# returns true for PROP_IS_ONE_OF number strings when the context value is the integer 1
def test_returns_true_for_prop_is_one_of_number_strings_when_the_context_value_is_the_integer_1(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-one-of.number", contexts={"user": {"n": 1}})
    assert result is True


# returns true for PROP_IS_ONE_OF number strings when the context value is the number 1.5
def test_returns_true_for_prop_is_one_of_number_strings_when_the_context_value_is_the_number_1_5(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-one-of.number", contexts={"user": {"n": 1.5}})
    assert result is True


# returns true for PROP_IS_ONE_OF number strings when the context value is the integer 1234567
def test_returns_true_for_prop_is_one_of_number_strings_when_the_context_value_is_the_integer_1234567(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.is-one-of.number", contexts={"user": {"n": 1234567}}
    )
    assert result is True


# returns false for PROP_IS_ONE_OF number strings when the context value is the integer 2
def test_returns_false_for_prop_is_one_of_number_strings_when_the_context_value_is_the_integer_2(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.is-one-of.number", contexts={"user": {"n": 2}})
    assert result is False


# returns true for PROP_ENDS_WITH_ONE_OF ["3"] when the context value is the integer 123
def test_returns_true_for_prop_ends_with_one_of_3_when_the_context_value_is_the_integer_123(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.number", contexts={"user": {"n": 123}}
    )
    assert result is True


# returns false for PROP_ENDS_WITH_ONE_OF ["3"] when the context value is the integer 124
def test_returns_false_for_prop_ends_with_one_of_3_when_the_context_value_is_the_integer_124(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.number", contexts={"user": {"n": 124}}
    )
    assert result is False


# returns true for IN_INT_RANGE [0, 10) when the context value is the start bound 0
def test_returns_true_for_in_int_range_0_10_when_the_context_value_is_the_start_bound_0(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.in-int-range", contexts={"user": {"n": 0}})
    assert result is True


# returns true for IN_INT_RANGE [0, 10) when the context value is 9
def test_returns_true_for_in_int_range_0_10_when_the_context_value_is_9(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.in-int-range", contexts={"user": {"n": 9}})
    assert result is True


# returns false for IN_INT_RANGE [0, 10) when the context value is the end bound 10
def test_returns_false_for_in_int_range_0_10_when_the_context_value_is_the_end_bound_10(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.in-int-range", contexts={"user": {"n": 10}})
    assert result is False


# returns false for IN_INT_RANGE [0, 10) when the context value is -1
def test_returns_false_for_in_int_range_0_10_when_the_context_value_is_1(config_client) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.in-int-range", contexts={"user": {"n": -1}})
    assert result is False


# returns true for IN_INT_RANGE with no end when the context value is 5000000000
def test_returns_true_for_in_int_range_with_no_end_when_the_context_value_is_5000000000(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-int-range.open-end", contexts={"user": {"n": 5000000000}}
    )
    assert result is True


# returns true for IN_INT_RANGE with no start when the context value is -5
def test_returns_true_for_in_int_range_with_no_start_when_the_context_value_is_5(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-int-range.open-start", contexts={"user": {"n": -5}}
    )
    assert result is True


# returns false for PROP_SEMVER_EQUAL 2.0.0 when the given prop is the prerelease 2.0.0-beta.1
def test_returns_false_for_prop_semver_equal_2_0_0_when_the_given_prop_is_the_prerelease_2_0_0_beta_1(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-equal", contexts={"app": {"version": "2.0.0-beta.1"}}
    )
    assert result is False


# returns true for PROP_SEMVER_LESS_THAN 2.0.0 when the given prop is the prerelease 2.0.0-beta.1
def test_returns_true_for_prop_semver_less_than_2_0_0_when_the_given_prop_is_the_prerelease_2_0_0_beta_1(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than", contexts={"app": {"version": "2.0.0-beta.1"}}
    )
    assert result is True


# returns false for PROP_SEMVER_GREATER_THAN 2.0.0 when the given prop is the prerelease 2.0.0-beta.1
def test_returns_false_for_prop_semver_greater_than_2_0_0_when_the_given_prop_is_the_prerelease_2_0_0_beta_1(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-greater-than", contexts={"app": {"version": "2.0.0-beta.1"}}
    )
    assert result is False


# returns true for PROP_SEMVER_EQUAL 2.0.0 when the given prop differs only by build metadata
def test_returns_true_for_prop_semver_equal_2_0_0_when_the_given_prop_differs_only_by_build_metadata(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-equal", contexts={"app": {"version": "2.0.0+build.5"}}
    )
    assert result is True


# returns true for PROP_SEMVER_LESS_THAN 2.0.0-beta when the given prop is the prerelease 2.0.0-alpha
def test_returns_true_for_prop_semver_less_than_2_0_0_beta_when_the_given_prop_is_the_prerelease_2_0_0_alpha(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than.prerelease", contexts={"app": {"version": "2.0.0-alpha"}}
    )
    assert result is True


# returns true for PROP_SEMVER_EQUAL 2.0.0 when the given prop is the abbreviated major version 2
def test_returns_true_for_prop_semver_equal_2_0_0_when_the_given_prop_is_the_abbreviated_major_version_2(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.semver-equal", contexts={"app": {"version": "2"}})
    assert result is True


# returns false for PROP_SEMVER_EQUAL 2.0.0 when the given prop has a v prefix
def test_returns_false_for_prop_semver_equal_2_0_0_when_the_given_prop_has_a_v_prefix(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-equal", contexts={"app": {"version": "v2.0.0"}}
    )
    assert result is False


# returns false for PROP_BEFORE when the given prop is a date-only string
def test_returns_false_for_prop_before_when_the_given_prop_is_a_date_only_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "2024-11-01"}}
    )
    assert result is False


# returns false for PROP_BEFORE when the given prop is an RFC3339 string without an offset
def test_returns_false_for_prop_before_when_the_given_prop_is_an_rfc3339_string_without_an_offset(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "2024-11-01T00:00:00"}}
    )
    assert result is False


# returns false for PROP_BEFORE when the given prop is a free-form date string
def test_returns_false_for_prop_before_when_the_given_prop_is_a_free_form_date_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "Nov 1 2024"}}
    )
    assert result is False


# returns true for PROP_BEFORE when the given prop is an RFC3339 string with a numeric offset
def test_returns_true_for_prop_before_when_the_given_prop_is_an_rfc3339_string_with_a_numeric_offset(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "2024-11-01T02:00:00+02:00"}}
    )
    assert result is True


# returns true for PROP_BEFORE when the given prop is an all-digit epoch-ms string
def test_returns_true_for_prop_before_when_the_given_prop_is_an_all_digit_epoch_ms_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "1730419200000"}}
    )
    assert result is True


# returns true for PROP_BEFORE an RFC3339 criterion when the given prop is an earlier epoch-ms number
def test_returns_true_for_prop_before_an_rfc3339_criterion_when_the_given_prop_is_an_earlier_epoch_ms_number(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before.rfc3339-criterion", contexts={"user": {"creation_date": 1730419200000}}
    )
    assert result is True


# returns false for PROP_BEFORE an RFC3339 criterion when the given prop is a later RFC3339 string
def test_returns_false_for_prop_before_an_rfc3339_criterion_when_the_given_prop_is_a_later_rfc3339_string(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before.rfc3339-criterion",
        contexts={"user": {"creation_date": "2025-01-01T00:00:00Z"}},
    )
    assert result is False


# returns false for PROP_AFTER an all-digit criterion "999" when the given prop is 500
def test_returns_false_for_prop_after_an_all_digit_criterion_999_when_the_given_prop_is_500(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after.numeric-string-criterion", contexts={"user": {"creation_date": 500}}
    )
    assert result is False


# returns true for PROP_AFTER an all-digit criterion "999" when the given prop is 1000
def test_returns_true_for_prop_after_an_all_digit_criterion_999_when_the_given_prop_is_1000(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after.numeric-string-criterion", contexts={"user": {"creation_date": 1000}}
    )
    assert result is True


# returns false for PROP_GREATER_THAN 30 when the given prop is a numeric string above the threshold
def test_returns_false_for_prop_greater_than_30_when_the_given_prop_is_a_numeric_string_above_the_threshold(
    config_client,
) -> None:
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": "35"}})
    assert result is False
