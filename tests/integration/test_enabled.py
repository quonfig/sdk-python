# AUTO-GENERATED from integration-test-data/tests/eval/enabled.yaml
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


def test_returns_the_correct_value_for_a_simple_flag(config_client):
    c = config_client
    result = c.is_feature_enabled("feature-flag.simple")
    assert result is True


def test_always_returns_false_for_a_non_boolean_flag(config_client):
    c = config_client
    result = c.is_feature_enabled("feature-flag.integer")
    assert result is False


def test_returns_true_for_a_prop_is_one_of_rule_when_any_prop_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.positive",
        contexts={"": {"name": "michael", "domain": "something.com"}},
    )
    assert result is True


def test_returns_false_for_a_prop_is_one_of_rule_when_no_prop_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.positive",
        contexts={"": {"name": "lauren", "domain": "something.com"}},
    )
    assert result is False


def test_returns_true_for_a_prop_is_not_one_of_rule_when_any_prop_doesn_t_match(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.negative",
        contexts={"": {"name": "lauren", "domain": "prefab.cloud"}},
    )
    assert result is True


def test_returns_false_for_a_prop_is_not_one_of_rule_when_all_props_match(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.properties.negative",
        contexts={"": {"name": "michael", "domain": "prefab.cloud"}},
    )
    assert result is False


def test_returns_true_for_prop_ends_with_one_of_rule_when_the_given_prop_has_a_matching_suffix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.positive", contexts={"": {"email": "jeff@prefab.cloud"}}
    )
    assert result is True


def test_returns_false_for_prop_ends_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_suffix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.positive", contexts={"": {"email": "jeff@test.com"}}
    )
    assert result is False


def test_returns_true_for_prop_does_not_end_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_suffix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.negative", contexts={"": {"email": "michael@test.com"}}
    )
    assert result is True


def test_returns_false_for_prop_does_not_end_with_one_of_rule_when_the_given_prop_has_a_matching_suffix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.ends-with-one-of.negative", contexts={"": {"email": "michael@prefab.cloud"}}
    )
    assert result is False


def test_returns_true_for_prop_starts_with_one_of_rule_when_the_given_prop_has_a_matching_prefix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.positive", contexts={"user": {"email": "foo@prefab.cloud"}}
    )
    assert result is True


def test_returns_false_for_prop_starts_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_prefix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.positive",
        contexts={"user": {"email": "notfoo@prefab.cloud"}},
    )
    assert result is False


def test_returns_true_for_prop_does_not_start_with_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_prefix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.negative",
        contexts={"user": {"email": "notfoo@prefab.cloud"}},
    )
    assert result is True


def test_returns_false_for_prop_does_not_start_with_one_of_rule_when_the_given_prop_has_a_matching_prefix(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.starts-with-one-of.negative", contexts={"user": {"email": "foo@prefab.cloud"}}
    )
    assert result is False


def test_returns_true_for_prop_contains_one_of_rule_when_the_given_prop_has_a_matching_substring(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.positive",
        contexts={"user": {"email": "somefoo@prefab.cloud"}},
    )
    assert result is True


def test_returns_false_for_prop_contains_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_substring(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.positive", contexts={"user": {"email": "info@prefab.cloud"}}
    )
    assert result is False


def test_returns_true_for_prop_does_not_contain_one_of_rule_when_the_given_prop_doesn_t_have_a_matching_substring(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.negative", contexts={"user": {"email": "info@prefab.cloud"}}
    )
    assert result is True


def test_returns_false_for_prop_does_not_contain_one_of_rule_when_the_given_prop_has_a_matching_substring(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.contains-one-of.negative", contexts={"user": {"email": "notfoo@prefab.cloud"}}
    )
    assert result is False


def test_returns_true_for_in_seg_when_the_segment_rule_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.positive", contexts={"user": {"key": "lauren"}}
    )
    assert result is True


def test_returns_false_for_in_seg_when_the_segment_rule_doesn_t_match(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.positive", contexts={"user": {"key": "josh"}}
    )
    assert result is False


def test_returns_false_for_in_seg_if_any_segment_rule_fails_to_match(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is False


def test_returns_true_for_in_seg_segment_and_if_all_rules_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"user": {"key": "michael"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is True


def test_returns_true_for_in_seg_segment_or_if_any_segment_rule_matches_lookup(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-or",
        contexts={"user": {"key": "michael"}, "": {"domain": "example.com"}},
    )
    assert result is True


def test_returns_true_for_in_seg_segment_or_if_any_segment_rule_matches_prop(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-or",
        contexts={"user": {"key": "nobody"}, "": {"domain": "gmail.com"}},
    )
    assert result is True


def test_returns_true_for_not_in_seg_when_the_segment_rule_doesn_t_match(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.negative", contexts={"user": {"key": "josh"}}
    )
    assert result is True


def test_returns_false_for_not_in_seg_when_the_segment_rule_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.negative", contexts={"user": {"key": "michael"}}
    )
    assert result is False


def test_returns_false_for_not_in_seg_if_any_segment_rule_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.multiple-criteria.negative",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is True


def test_returns_true_for_not_in_seg_if_no_segment_rule_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-segment.multiple-criteria.negative",
        contexts={"user": {"key": "josh"}, "": {"domain": "something.com"}},
    )
    assert result is True


def test_returns_true_for_not_in_seg_segment_and_if_not_segment_rule_fails_to_match(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-and",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is True


def test_returns_true_for_in_seg_segment_and_if_not_segment_rule_fails_to_match(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.in-seg.segment-and",
        contexts={"user": {"key": "josh"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is False


def test_returns_false_for_not_in_seg_segment_and_if_segment_rules_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-and",
        contexts={"user": {"key": "michael"}, "": {"domain": "prefab.cloud"}},
    )
    assert result is False


def test_returns_true_for_not_in_seg_segment_or_if_no_segment_rule_matches(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-or",
        contexts={"user": {"key": "nobody"}, "": {"domain": "example.com"}},
    )
    assert result is True


def test_returns_false_for_not_in_seg_segment_or_if_one_segment_rule_matches_prop(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-or",
        contexts={"user": {"key": "nobody"}, "": {"domain": "gmail.com"}},
    )
    assert result is False


def test_returns_false_for_not_in_seg_segment_or_if_one_segment_rule_matches_lookup(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.not-in-seg.segment-or",
        contexts={"user": {"key": "michael"}, "": {"domain": "example.com"}},
    )
    assert result is False


def test_returns_true_for_prop_before_rule_when_the_given_prop_represents_a_date_string_before_the_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "2024-11-01T00:00:00Z"}}
    )
    assert result is True


def test_returns_true_for_prop_before_rule_when_the_given_prop_represents_a_date_number_before_the_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": 1730419200000}}
    )
    assert result is True


def test_returns_false_for_prop_before_rule_when_the_given_prop_represents_a_date_number_exactly_matching_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": 1733011200000}}
    )
    assert result is False


def test_returns_false_for_prop_before_rule_when_the_given_prop_represents_a_date_number_after_the_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "2025-01-01T00:00:00Z"}}
    )
    assert result is False


def test_returns_false_for_prop_before_rule_when_the_given_prop_won_t_parse_as_a_date(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.before", contexts={"user": {"creation_date": "not a date"}}
    )
    assert result is False


def test_returns_false_for_prop_before_rule_using_current_time_relative_to_2050_01_01(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.before.current-time")
    assert result is True


def test_returns_true_for_prop_after_rule_when_the_given_prop_represents_a_date_string_after_the_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": "2025-01-01T00:00:00Z"}}
    )
    assert result is True


def test_returns_true_for_prop_after_rule_when_the_given_prop_represents_a_date_number_after_the_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": 1735689600000}}
    )
    assert result is True


def test_returns_false_for_prop_after_rule_when_the_given_prop_represents_a_date_number_exactly_matching_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": 1733011200000}}
    )
    assert result is False


def test_returns_false_for_prop_before_rule_when_the_given_prop_represents_a_date_number_before_the_rule_s_time(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": "2024-01-01T00:00:00Z"}}
    )
    assert result is False


def test_returns_false_for_prop_after_rule_when_the_given_prop_won_t_parse_as_a_date(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.after", contexts={"user": {"creation_date": "not a date"}}
    )
    assert result is False


def test_returns_false_for_prop_after_rule_using_current_time_relative_to_2025_01_01(config_client):
    c = config_client
    result = c.is_feature_enabled("feature-flag.after.current-time")
    assert result is True


def test_returns_true_for_prop_less_than_rule_when_the_given_prop_is_less_than_the_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": 20}})
    assert result is True


def test_returns_true_for_prop_less_than_rule_when_the_given_prop_is_less_than_the_rule_s_value_float(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": 20.5}})
    assert result is True


def test_returns_false_for_prop_less_than_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": 30}})
    assert result is False


def test_returns_false_for_prop_less_than_rule_when_the_given_prop_a_string(config_client):
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than", contexts={"user": {"age": "20"}})
    assert result is False


def test_returns_true_for_prop_less_than_or_equal_rule_when_the_given_prop_is_less_than_the_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than-or-equal", contexts={"user": {"age": 20}})
    assert result is True


def test_returns_true_for_prop_less_than_or_equal_rule_when_the_given_prop_is_less_than_the_rule_s_value_float(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.less-than-or-equal", contexts={"user": {"age": 20.5}}
    )
    assert result is True


def test_returns_false_for_prop_less_than_or_equal_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.less-than-or-equal", contexts={"user": {"age": 30}})
    assert result is True


def test_returns_false_for_prop_less_than_or_equal_rule_when_the_given_prop_a_string(config_client):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.less-than-or-equal", contexts={"user": {"age": "20"}}
    )
    assert result is False


def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": 100}})
    assert result is True


def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_value_float(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": 30.5}})
    assert result is True


def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_float_value_float(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than.double", contexts={"user": {"age": 32.7}}
    )
    assert result is True


def test_returns_true_for_prop_greater_than_rule_when_the_given_prop_is_greater_than_the_rule_s_float_value_integer(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than.double", contexts={"user": {"age": 32}}
    )
    assert result is True


def test_returns_false_for_prop_greater_than_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": 30}})
    assert result is False


def test_returns_false_for_prop_greater_than_rule_when_the_given_prop_a_string(config_client):
    c = config_client
    result = c.is_feature_enabled("feature-flag.greater-than", contexts={"user": {"age": "100"}})
    assert result is False


def test_returns_true_for_prop_greater_than_or_equal_rule_when_the_given_prop_is_greater_than_the_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": 30}}
    )
    assert result is True


def test_returns_true_for_prop_greater_than_or_equal_rule_when_the_given_prop_is_greater_than_the_rule_s_value_float(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": 30.5}}
    )
    assert result is True


def test_returns_true_for_prop_greater_than_or_equal_rule_when_the_given_prop_is_equal_to_rule_s_value(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": 30}}
    )
    assert result is True


def test_returns_false_for_prop_greater_than_or_equal_rule_when_the_given_prop_a_string(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.greater-than-or-equal", contexts={"user": {"age": "100"}}
    )
    assert result is False


def test_returns_true_for_prop_matches_rule_when_the_given_prop_matches_the_regex(config_client):
    c = config_client
    result = c.is_feature_enabled("feature-flag.matches", contexts={"user": {"code": "aaaaaab"}})
    assert result is True


def test_returns_false_for_prop_matches_rule_when_the_given_prop_does_not_match_the_regex(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.matches", contexts={"user": {"code": "aa"}})
    assert result is False


def test_returns_true_for_prop_does_not_match_rule_when_the_given_prop_does_not_match_the_regex(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.does-not-match", contexts={"user": {"code": "b"}})
    assert result is True


def test_returns_false_for_prop_does_not_match_rule_when_the_given_prop_matches_the_regex(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.does-not-match", contexts={"user": {"code": "aabb"}}
    )
    assert result is False


def test_returns_true_for_prop_semver_equal_rule_when_the_given_prop_equals_the_version(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-equal", contexts={"app": {"version": "2.0.0"}}
    )
    assert result is True


def test_returns_false_for_prop_semver_equal_rule_when_the_given_prop_does_not_equal_the_version(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-equal", contexts={"app": {"version": "2.0.1"}}
    )
    assert result is False


def test_returns_false_for_prop_semver_equal_rule_when_the_given_prop_is_not_a_valid_semver(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled("feature-flag.semver-equal", contexts={"app": {"version": "2.0"}})
    assert result is False


def test_returns_true_for_prop_semver_less_than_rule_when_the_given_prop_is_less_than_2_0_0(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than", contexts={"app": {"version": "1.5.1"}}
    )
    assert result is True


def test_returns_false_for_prop_semver_less_than_rule_when_the_given_prop_equals_the_version(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than", contexts={"app": {"version": "2.0.0"}}
    )
    assert result is False


def test_returns_false_for_prop_semver_less_than_rule_when_the_given_prop_is_greater_than_the_version(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-less-than", contexts={"app": {"version": "2.2.1"}}
    )
    assert result is False


def test_returns_true_for_prop_semver_greater_than_rule_when_the_given_prop_is_greater_than_2_0_0(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-greater-than", contexts={"app": {"version": "2.5.1"}}
    )
    assert result is True


def test_returns_false_for_prop_semver_greater_than_rule_when_the_given_prop_equals_the_version(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-greater-than", contexts={"app": {"version": "2.0.0"}}
    )
    assert result is False


def test_returns_false_for_prop_semver_equal_rule_when_the_given_prop_is_less_than_the_version(
    config_client,
):
    c = config_client
    result = c.is_feature_enabled(
        "feature-flag.semver-greater-than", contexts={"app": {"version": "0.0.5"}}
    )
    assert result is False
