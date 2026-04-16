# AUTO-GENERATED from integration-test-data/tests/eval/telemetry.yaml. Do not edit by hand.
"""Telemetry aggregator integration tests with reason assertions."""
from __future__ import annotations

import pytest

from .telemetry_helpers import (
    ContextShapeCollector,
    EvaluationSummaryCollector,
    ExampleContextCollector,
    evaluate_for_telemetry,
)

REASON_STATIC = 1
REASON_TARGETING_MATCH = 2
REASON_SPLIT = 3


# ──────────────────────────────────────────────────────────────────────────────
# Category 1: Evaluation Reason Reporting
# ──────────────────────────────────────────────────────────────────────────────

def test_reason_is_static_for_config_with_no_targeting_rules():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("brand.new.string")
    assert result is not None
    collector.record(result)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "brand.new.string")
    assert s.type == "config"
    assert s.counters[0].count == 1
    assert s.counters[0].config_row_index == 0
    assert s.counters[0].conditional_value_index == 0
    assert s.counters[0].reason == REASON_STATIC


def test_reason_is_static_for_feature_flag_with_only_always_true_rules():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("always.true")
    assert result is not None
    collector.record(result)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "always.true")
    assert s.type == "feature_flag"
    assert s.counters[0].count == 1
    assert s.counters[0].reason == REASON_STATIC


def test_reason_is_targeting_match_when_config_has_targeting_rules_but_evaluation_falls_through():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("my-test-key")
    assert result is not None
    collector.record(result)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "my-test-key")
    assert s.type == "config"
    assert s.counters[0].count == 1
    assert s.counters[0].conditional_value_index == 1
    assert s.counters[0].reason == REASON_TARGETING_MATCH


def test_reason_is_targeting_match_when_a_targeting_rule_matches():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("feature-flag.integer", {"user": {"key": "michael"}})
    assert result is not None
    collector.record(result)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "feature-flag.integer")
    assert s.type == "feature_flag"
    assert s.counters[0].count == 1
    assert s.counters[0].conditional_value_index == 0
    assert s.counters[0].reason == REASON_TARGETING_MATCH


def test_reason_is_split_for_weighted_value_evaluation():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("feature-flag.weighted", {"user": {"tracking_id": "92a202f2"}})
    assert result is not None
    collector.record(result)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "feature-flag.weighted")
    assert s.type == "feature_flag"
    assert s.counters[0].count == 1
    assert s.counters[0].weighted_value_index == 2
    assert s.counters[0].reason == REASON_SPLIT


def test_reason_is_targeting_match_for_feature_flag_fallthrough_with_targeting_rules():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("feature-flag.integer")
    assert result is not None
    collector.record(result)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "feature-flag.integer")
    assert s.type == "feature_flag"
    assert s.counters[0].count == 1
    assert s.counters[0].conditional_value_index == 1
    assert s.counters[0].reason == REASON_TARGETING_MATCH


# ──────────────────────────────────────────────────────────────────────────────
# Category 2: Counting & Grouping
# ──────────────────────────────────────────────────────────────────────────────

def test_evaluation_summary_deduplicates_identical_evaluations():
    collector = EvaluationSummaryCollector(enabled=True)
    for _ in range(5):
        result = evaluate_for_telemetry("brand.new.string")
        assert result is not None
        collector.record(result)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "brand.new.string")
    assert len(s.counters) == 1
    assert s.counters[0].count == 5


def test_evaluation_summary_creates_separate_counters_for_different_rules_of_same_config():
    collector = EvaluationSummaryCollector(enabled=True)
    r1 = evaluate_for_telemetry("feature-flag.integer", {"user": {"key": "michael"}})
    r2 = evaluate_for_telemetry("feature-flag.integer")
    assert r1 is not None and r2 is not None
    collector.record(r1)
    collector.record(r2)

    event = collector.drain()
    assert event is not None
    summaries = event.summaries.summaries
    s = next(x for x in summaries if x.key == "feature-flag.integer")
    assert len(s.counters) == 2
    idx0 = next(c for c in s.counters if c.conditional_value_index == 0)
    idx1 = next(c for c in s.counters if c.conditional_value_index == 1)
    assert idx0.count == 1
    assert idx1.count == 1


def test_evaluation_summary_groups_by_config_key():
    collector = EvaluationSummaryCollector(enabled=True)
    for key in ("brand.new.string", "always.true"):
        r = evaluate_for_telemetry(key)
        assert r is not None
        collector.record(r)

    event = collector.drain()
    assert event is not None
    keys = {s.key for s in event.summaries.summaries}
    assert "brand.new.string" in keys
    assert "always.true" in keys


# ──────────────────────────────────────────────────────────────────────────────
# Category 3: selectedValue Type Wrapping
# ──────────────────────────────────────────────────────────────────────────────

def test_selected_value_wraps_string_correctly():
    collector = EvaluationSummaryCollector(enabled=True)
    collector.record(evaluate_for_telemetry("brand.new.string"))
    event = collector.drain()
    s = next(x for x in event.summaries.summaries if x.key == "brand.new.string")
    assert s.counters[0].selected_value == {"string": "hello.world"}


def test_selected_value_wraps_boolean_correctly():
    collector = EvaluationSummaryCollector(enabled=True)
    collector.record(evaluate_for_telemetry("brand.new.boolean"))
    event = collector.drain()
    s = next(x for x in event.summaries.summaries if x.key == "brand.new.boolean")
    assert s.counters[0].selected_value == {"bool": False}


def test_selected_value_wraps_int_correctly():
    collector = EvaluationSummaryCollector(enabled=True)
    collector.record(evaluate_for_telemetry("brand.new.int"))
    event = collector.drain()
    s = next(x for x in event.summaries.summaries if x.key == "brand.new.int")
    assert s.counters[0].selected_value == {"int": 123}


def test_selected_value_wraps_double_correctly():
    collector = EvaluationSummaryCollector(enabled=True)
    collector.record(evaluate_for_telemetry("brand.new.double"))
    event = collector.drain()
    s = next(x for x in event.summaries.summaries if x.key == "brand.new.double")
    assert abs(s.counters[0].selected_value["double"] - 123.99) < 1e-9


def test_selected_value_wraps_string_list_correctly():
    collector = EvaluationSummaryCollector(enabled=True)
    collector.record(evaluate_for_telemetry("my-string-list-key"))
    event = collector.drain()
    s = next(x for x in event.summaries.summaries if x.key == "my-string-list-key")
    assert s.counters[0].selected_value == {"stringList": ["a", "b", "c"]}


# ──────────────────────────────────────────────────────────────────────────────
# Category 4: Context Telemetry
# ──────────────────────────────────────────────────────────────────────────────

def test_context_shape_merges_fields_across_multiple_records():
    collector = ContextShapeCollector(context_upload_mode="shapes_only")
    collector.record({"user": {"name": "alice", "age": 30}})
    collector.record({"user": {"name": "bob", "score": 9.5}, "team": {"name": "engineering"}})

    event = collector.drain()
    assert event is not None
    shapes = {s.name: s.field_types for s in event.context_shapes.shapes}

    assert shapes["user"]["name"] == 2   # string
    assert shapes["user"]["age"] == 1    # int
    assert shapes["user"]["score"] == 4  # double
    assert shapes["team"]["name"] == 2   # string


def test_example_contexts_deduplicates_by_key_value():
    collector = ExampleContextCollector(context_upload_mode="periodic_example", rate_limit_ms=60_000)
    collector.record({"user": {"key": "user-123", "name": "alice"}})
    collector.record({"user": {"key": "user-123", "name": "bob"}})

    event = collector.drain()
    assert event is not None
    examples = event.example_contexts.examples
    assert len(examples) == 1
    user_ctx = next(c for c in examples[0].context_set["contexts"] if c["type"] == "user")
    assert user_ctx["values"]["key"] == "user-123"
    assert user_ctx["values"]["name"] == "alice"


# ──────────────────────────────────────────────────────────────────────────────
# Category 5: Configuration Modes
# ──────────────────────────────────────────────────────────────────────────────

def test_telemetry_disabled_emits_nothing():
    collector = EvaluationSummaryCollector(enabled=False)
    result = evaluate_for_telemetry("brand.new.string")
    assert result is not None
    collector.record(result)
    assert collector.drain() is None


def test_shapes_only_mode_reports_shapes_but_not_examples():
    shape_collector = ContextShapeCollector(context_upload_mode="shapes_only")
    shape_collector.record({"user": {"name": "alice", "key": "alice-123"}})
    event = shape_collector.drain()
    assert event is not None
    shapes = {s.name: s.field_types for s in event.context_shapes.shapes}
    assert shapes["user"]["name"] == 2
    assert shapes["user"]["key"] == 2

    example_collector = ExampleContextCollector(context_upload_mode="shapes_only")
    example_collector.record({"user": {"key": "alice-123", "name": "alice"}})
    assert example_collector.drain() is None


# ──────────────────────────────────────────────────────────────────────────────
# Category 6: Edge Cases
# ──────────────────────────────────────────────────────────────────────────────

def test_log_level_evaluations_are_excluded_from_telemetry():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("log-level.prefab.criteria_evaluator")
    assert result is not None
    collector.record(result)
    assert collector.drain() is None


def test_empty_context_produces_no_context_telemetry():
    collector = ContextShapeCollector(context_upload_mode="shapes_only")
    collector.record({})
    assert collector.drain() is None
