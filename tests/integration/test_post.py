# AUTO-GENERATED from integration-test-data/tests/eval/post.yaml. Do not edit by hand.
"""Post (combined) telemetry integration tests."""
from __future__ import annotations

from .telemetry_helpers import (
    ContextShapeCollector,
    EvaluationSummaryCollector,
    ExampleContextCollector,
    evaluate_for_telemetry,
)

REASON_STATIC = 1
REASON_TARGETING_MATCH = 2
REASON_SPLIT = 3


def test_reports_context_shape_aggregation():
    collector = ContextShapeCollector(context_upload_mode="shapes_only")
    collector.record({
        "user": {"name": "Michael", "age": 38, "human": True},
        "role": {"name": "developer", "admin": False, "salary": 15.75, "permissions": ["read", "write"]},
    })

    event = collector.drain()
    assert event is not None
    shapes = {s.name: s.field_types for s in event.context_shapes.shapes}

    assert shapes["user"]["name"] == 2   # string
    assert shapes["user"]["age"] == 1    # int
    assert shapes["user"]["human"] == 5  # bool
    assert shapes["role"]["name"] == 2
    assert shapes["role"]["admin"] == 5
    assert shapes["role"]["salary"] == 4  # double
    assert shapes["role"]["permissions"] == 10  # array


def test_reports_evaluation_summary():
    collector = EvaluationSummaryCollector(enabled=True)
    ctx = {"user": {"tracking_id": "92a202f2"}}

    for key in ("my-test-key", "feature-flag.integer", "my-string-list-key", "feature-flag.integer", "feature-flag.weighted"):
        r = evaluate_for_telemetry(key, ctx)
        assert r is not None
        collector.record(r)

    event = collector.drain()
    assert event is not None
    summaries = {s.key: s for s in event.summaries.summaries}

    # my-test-key: count 1, reason TARGETING_MATCH, conditionalValueIndex 1
    tk = summaries["my-test-key"]
    assert tk.counters[0].count == 1
    assert tk.counters[0].reason == REASON_TARGETING_MATCH
    assert tk.counters[0].conditional_value_index == 1

    # my-string-list-key: count 1, reason STATIC
    sl = summaries["my-string-list-key"]
    assert sl.counters[0].count == 1
    assert sl.counters[0].reason == REASON_STATIC

    # feature-flag.integer: count 2 (evaluated twice), reason TARGETING_MATCH
    fi = summaries["feature-flag.integer"]
    assert fi.counters[0].count == 2
    assert fi.counters[0].reason == REASON_TARGETING_MATCH

    # feature-flag.weighted: count 1, reason SPLIT, weightedValueIndex 2
    fw = summaries["feature-flag.weighted"]
    assert fw.counters[0].count == 1
    assert fw.counters[0].reason == REASON_SPLIT
    assert fw.counters[0].weighted_value_index == 2


def test_reports_example_contexts():
    collector = ExampleContextCollector(context_upload_mode="periodic_example", rate_limit_ms=0)
    collector.record({"user": {"name": "michael", "age": 38, "key": "michael:1234"}, "device": {"mobile": False}, "team": {"id": 3.5}})

    event = collector.drain()
    assert event is not None
    examples = event.example_contexts.examples
    assert len(examples) == 1
    by_type = {c["type"]: c["values"] for c in examples[0].context_set["contexts"]}

    assert by_type["user"]["name"] == "michael"
    assert by_type["user"]["age"] == 38
    assert by_type["user"]["key"] == "michael:1234"
    assert by_type["device"]["mobile"] is False
    assert by_type["team"]["id"] == 3.5


def test_example_contexts_without_key_are_not_reported():
    collector = ExampleContextCollector(context_upload_mode="periodic_example", rate_limit_ms=0)
    collector.record({"user": {"name": "michael", "age": 38}, "device": {"mobile": False}, "team": {"id": 3.5}})
    assert collector.drain() is None
