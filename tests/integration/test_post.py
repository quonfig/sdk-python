# AUTO-GENERATED from integration-test-data/tests/eval/post.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

from .aggregator_helpers import aggregator_post, build_aggregator, feed_aggregator


# reports context shape aggregation
def test_reports_context_shape_aggregation() -> None:
    agg = build_aggregator('context_shape', {'context_upload_mode': ':shape_only'})
    feed_aggregator(agg, 'context_shape', {'user': {'name': 'Michael', 'age': 38, 'human': True}, 'role': {'name': 'developer', 'admin': False, 'salary': 15.75, 'permissions': ['read', 'write']}}, contexts={})
    assert aggregator_post(agg, 'context_shape', endpoint='/api/v1/context-shapes') == [{'name': 'user', 'field_types': {'name': 2, 'age': 1, 'human': 5}}, {'name': 'role', 'field_types': {'name': 2, 'admin': 5, 'salary': 4, 'permissions': 10}}]


# reports evaluation summary
def test_reports_evaluation_summary() -> None:
    agg = build_aggregator('evaluation_summary', {})
    feed_aggregator(agg, 'evaluation_summary', {'keys': ['my-test-key', 'feature-flag.integer', 'my-string-list-key', 'feature-flag.integer', 'feature-flag.weighted']}, contexts={'user': {'tracking_id': '92a202f2'}})
    assert aggregator_post(agg, 'evaluation_summary', endpoint='/api/v1/telemetry') == [{'key': 'my-test-key', 'type': 'CONFIG', 'value': 'my-test-value', 'value_type': 'string', 'count': 1, 'reason': 2, 'selected_value': {'string': 'my-test-value'}, 'summary': {'config_row_index': 0, 'conditional_value_index': 1}}, {'key': 'my-string-list-key', 'type': 'CONFIG', 'value': ['a', 'b', 'c'], 'value_type': 'string_list', 'count': 1, 'reason': 1, 'selected_value': {'stringList': ['a', 'b', 'c']}, 'summary': {'config_row_index': 0, 'conditional_value_index': 0}}, {'key': 'feature-flag.integer', 'type': 'FEATURE_FLAG', 'value': 3, 'value_type': 'int', 'count': 2, 'reason': 2, 'selected_value': {'int': 3}, 'summary': {'config_row_index': 0, 'conditional_value_index': 1}}, {'key': 'feature-flag.weighted', 'type': 'FEATURE_FLAG', 'value': 2, 'value_type': 'int', 'count': 1, 'reason': 3, 'selected_value': {'int': 2}, 'summary': {'config_row_index': 0, 'conditional_value_index': 0, 'weighted_value_index': 2}}]


# reports example contexts
def test_reports_example_contexts() -> None:
    agg = build_aggregator('example_contexts', {})
    feed_aggregator(agg, 'example_contexts', {'user': {'name': 'michael', 'age': 38, 'key': 'michael:1234'}, 'device': {'mobile': False}, 'team': {'id': 3.5}}, contexts={})
    assert aggregator_post(agg, 'example_contexts', endpoint='/api/v1/telemetry') == {'user': {'name': 'michael', 'age': 38, 'key': 'michael:1234'}, 'device': {'mobile': False}, 'team': {'id': 3.5}}


# example contexts without key are not reported
def test_example_contexts_without_key_are_not_reported() -> None:
    agg = build_aggregator('example_contexts', {})
    feed_aggregator(agg, 'example_contexts', {'user': {'name': 'michael', 'age': 38}, 'device': {'mobile': False}, 'team': {'id': 3.5}}, contexts={})
    assert aggregator_post(agg, 'example_contexts', endpoint='/api/v1/telemetry') is None
