# AUTO-GENERATED from integration-test-data/tests/eval/telemetry.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

import os

import pytest

from .aggregator_helpers import build_aggregator, feed_aggregator, aggregator_post

# reason is STATIC for config with no targeting rules
def test_reason_is_static_for_config_with_no_targeting_rules() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.string']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'brand.new.string', 'type': 'CONFIG', 'value': 'hello.world', 'value_type': 'string', 'count': 1, 'reason': 1, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# reason is STATIC for feature flag with only ALWAYS_TRUE rules
def test_reason_is_static_for_feature_flag_with_only_always_true_rules() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['always.true']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'always.true', 'type': 'FEATURE_FLAG', 'value': True, 'value_type': 'bool', 'count': 1, 'reason': 1, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# reason is TARGETING_MATCH when config has targeting rules but evaluation falls through
def test_reason_is_targeting_match_when_config_has_targeting_rules_but_evaluation_falls_through() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['my-test-key']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'my-test-key', 'type': 'CONFIG', 'value': 'my-test-value', 'value_type': 'string', 'count': 1, 'reason': 2, 'summary': {'config_row_index': 0, 'conditional_value_index': 1}}]


# reason is TARGETING_MATCH when a targeting rule matches
def test_reason_is_targeting_match_when_a_targeting_rule_matches() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['feature-flag.integer']}, contexts={'user': {'key': 'michael'}})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'feature-flag.integer', 'type': 'FEATURE_FLAG', 'value': 5, 'value_type': 'int', 'count': 1, 'reason': 2, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# reason is SPLIT for weighted value evaluation
def test_reason_is_split_for_weighted_value_evaluation() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['feature-flag.weighted']}, contexts={'user': {'tracking_id': '92a202f2'}})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'feature-flag.weighted', 'type': 'FEATURE_FLAG', 'value': 2, 'value_type': 'int', 'count': 1, 'reason': 3, 'summary': {'config_row_index': 0, 'conditional_value_index': 0, 'weighted_value_index': 2}}]


# reason is TARGETING_MATCH for feature flag fallthrough with targeting rules
def test_reason_is_targeting_match_for_feature_flag_fallthrough_with_targeting_rules() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['feature-flag.integer']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'feature-flag.integer', 'type': 'FEATURE_FLAG', 'value': 3, 'value_type': 'int', 'count': 1, 'reason': 2, 'summary': {'config_row_index': 0, 'conditional_value_index': 1}}]


# evaluation summary deduplicates identical evaluations
def test_evaluation_summary_deduplicates_identical_evaluations() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.string', 'brand.new.string', 'brand.new.string', 'brand.new.string', 'brand.new.string']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'brand.new.string', 'type': 'CONFIG', 'value': 'hello.world', 'value_type': 'string', 'count': 5, 'reason': 1, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# evaluation summary creates separate counters for different rules of same config
def test_evaluation_summary_creates_separate_counters_for_different_rules_of_same_config() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['feature-flag.integer'], 'keys_without_context': ['feature-flag.integer']}, contexts={'user': {'key': 'michael'}})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'feature-flag.integer', 'type': 'FEATURE_FLAG', 'value': 5, 'value_type': 'int', 'count': 1, 'reason': 2, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}, {'key': 'feature-flag.integer', 'type': 'FEATURE_FLAG', 'value': 3, 'value_type': 'int', 'count': 1, 'reason': 2, 'summary': {'config_row_index': 0, 'conditional_value_index': 1}}]


# evaluation summary groups by config key
def test_evaluation_summary_groups_by_config_key() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.string', 'always.true']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'brand.new.string', 'type': 'CONFIG', 'value': 'hello.world', 'value_type': 'string', 'count': 1, 'reason': 1, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}, {'key': 'always.true', 'type': 'FEATURE_FLAG', 'value': True, 'value_type': 'bool', 'count': 1, 'reason': 1, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# selectedValue wraps string correctly
def test_selectedvalue_wraps_string_correctly() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.string']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'brand.new.string', 'type': 'CONFIG', 'value': 'hello.world', 'value_type': 'string', 'count': 1, 'reason': 1, 'selected_value': {'string': 'hello.world'}, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# selectedValue wraps boolean correctly
def test_selectedvalue_wraps_boolean_correctly() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.boolean']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'brand.new.boolean', 'type': 'CONFIG', 'value': False, 'value_type': 'bool', 'count': 1, 'reason': 1, 'selected_value': {'bool': False}, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# selectedValue wraps int correctly
def test_selectedvalue_wraps_int_correctly() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.int']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'brand.new.int', 'type': 'CONFIG', 'value': 123, 'value_type': 'int', 'count': 1, 'reason': 1, 'selected_value': {'int': 123}, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# selectedValue wraps double correctly
def test_selectedvalue_wraps_double_correctly() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.double']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'brand.new.double', 'type': 'CONFIG', 'value': 123.99, 'value_type': 'double', 'count': 1, 'reason': 1, 'selected_value': {'double': 123.99}, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# selectedValue wraps string list correctly
def test_selectedvalue_wraps_string_list_correctly() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['my-string-list-key']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'my-string-list-key', 'type': 'CONFIG', 'value': ['a', 'b', 'c'], 'value_type': 'string_list', 'count': 1, 'reason': 1, 'selected_value': {'stringList': ['a', 'b', 'c']}, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}]


# context shape merges fields across multiple records
def test_context_shape_merges_fields_across_multiple_records() -> None:
    agg = build_aggregator('context_shape', {})
    feed_aggregator(agg, 'context_shape', [{'user': {'name': 'alice', 'age': 30}}, {'user': {'name': 'bob', 'score': 9.5}, 'team': {'name': 'engineering'}}], contexts={})
    assert aggregator_post(agg, 'context_shape', endpoint='/api/v1/context-shapes') == [{'name': 'user', 'field_types': {'name': 2, 'age': 1, 'score': 4}}, {'name': 'team', 'field_types': {'name': 2}}]


# example contexts deduplicates by key value
def test_example_contexts_deduplicates_by_key_value() -> None:
    agg = build_aggregator('example_contexts', {})
    feed_aggregator(agg, 'example_contexts', [{'user': {'key': 'user-123', 'name': 'alice'}}, {'user': {'key': 'user-123', 'name': 'bob'}}], contexts={})
    assert aggregator_post(agg, 'example_contexts', endpoint='/api/v1/telemetry') == {'user': {'key': 'user-123', 'name': 'alice'}}


# telemetry disabled emits nothing
def test_telemetry_disabled_emits_nothing() -> None:
    agg = build_aggregator('evaluation_summary', {'collect_evaluation_summaries': False, 'context_upload_mode': ':none'})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['brand.new.string']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == None


# shapes only mode reports shapes but not examples
def test_shapes_only_mode_reports_shapes_but_not_examples() -> None:
    agg = build_aggregator('context_shape', {'context_upload_mode': ':shape_only'})
    feed_aggregator(agg, 'context_shape', {'user': {'name': 'alice', 'key': 'alice-123'}}, contexts={})
    assert aggregator_post(agg, 'context_shape', endpoint='/api/v1/context-shapes') == [{'name': 'user', 'field_types': {'name': 2, 'key': 2}}]


# log level evaluations are excluded from telemetry
def test_log_level_evaluations_are_excluded_from_telemetry() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['log-level.prefab.criteria_evaluator']}, contexts={})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == None


# empty context produces no context telemetry
def test_empty_context_produces_no_context_telemetry() -> None:
    agg = build_aggregator('context_shape', {})
    feed_aggregator(agg, 'context_shape', {}, contexts={})
    assert aggregator_post(agg, 'context_shape', endpoint='/api/v1/context-shapes') == None
