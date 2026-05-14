"""JSON wire format snapshot tests for the telemetry payload.

Verifies that the serialized JSON matches the expected structure that
api-telemetry accepts, including field names, nesting, and reason.
"""

from __future__ import annotations

import json

from quonfig.telemetry.models import TelemetryPayload

from .telemetry_helpers import (
    ContextShapeCollector,
    EvaluationSummaryCollector,
    evaluate_for_telemetry,
)


def test_evaluation_summary_json_contains_required_fields_including_reason():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("brand.new.string")
    assert result is not None
    collector.record(result)

    event = collector.drain()
    assert event is not None

    payload = TelemetryPayload(instance_hash="test-hash", events=[event])
    doc = json.loads(json.dumps(payload.to_dict()))

    assert doc["instanceHash"] == "test-hash"
    assert len(doc["events"]) == 1

    summaries_block = doc["events"][0]["summaries"]
    assert "start" in summaries_block
    assert "end" in summaries_block
    assert len(summaries_block["summaries"]) == 1

    summary = summaries_block["summaries"][0]
    assert summary["key"] == "brand.new.string"
    assert summary["type"] == "config"
    assert len(summary["counters"]) == 1

    counter = summary["counters"][0]
    assert "configId" in counter
    assert "conditionalValueIndex" in counter
    assert "configRowIndex" in counter
    assert "selectedValue" in counter
    assert "count" in counter
    assert "reason" in counter
    assert isinstance(counter["reason"], int)
    assert counter["reason"] == 1  # STATIC


def test_selected_value_uses_correct_type_wrapper_keys_in_json():
    for key, expected_wrapper in [
        ("brand.new.string", "string"),
        ("brand.new.boolean", "bool"),
        ("brand.new.int", "int"),
        ("brand.new.double", "double"),
        ("my-string-list-key", "stringList"),
    ]:
        collector = EvaluationSummaryCollector(enabled=True)
        collector.record(evaluate_for_telemetry(key))
        event = collector.drain()
        doc = json.loads(json.dumps(event.summaries.summaries[0].counters[0].selected_value))
        assert expected_wrapper in doc, (
            f"{key}: expected wrapper key '{expected_wrapper}', got {doc}"
        )


def test_context_shapes_json_uses_field_types_not_field_names():
    collector = ContextShapeCollector(context_upload_mode="shapes_only")
    collector.record({"user": {"name": "alice", "age": 30, "active": True}})
    event = collector.drain()
    assert event is not None

    payload = TelemetryPayload(instance_hash="x", events=[event])
    doc = json.loads(json.dumps(payload.to_dict()))

    shapes_block = doc["events"][0]["contextShapes"]
    assert "shapes" in shapes_block
    shape = next(s for s in shapes_block["shapes"] if s["name"] == "user")
    assert "fieldTypes" in shape
    assert shape["fieldTypes"]["name"] == 2  # string
    assert shape["fieldTypes"]["age"] == 1  # int
    assert shape["fieldTypes"]["active"] == 5  # bool


def test_weighted_value_index_appears_in_json_when_present():
    collector = EvaluationSummaryCollector(enabled=True)
    result = evaluate_for_telemetry("feature-flag.weighted", {"user": {"tracking_id": "92a202f2"}})
    assert result is not None
    collector.record(result)

    event = collector.drain()

    payload = TelemetryPayload(instance_hash="x", events=[event])
    full_doc = json.loads(json.dumps(payload.to_dict()))
    counter = full_doc["events"][0]["summaries"]["summaries"][0]["counters"][0]
    assert counter["weightedValueIndex"] == 2
    assert counter["reason"] == 3  # SPLIT


def test_reason_absent_fields_not_in_json_for_non_weighted():
    collector = EvaluationSummaryCollector(enabled=True)
    collector.record(evaluate_for_telemetry("brand.new.string"))
    event = collector.drain()

    payload = TelemetryPayload(instance_hash="x", events=[event])
    doc = json.loads(json.dumps(payload.to_dict()))
    counter = doc["events"][0]["summaries"]["summaries"][0]["counters"][0]
    assert "weightedValueIndex" not in counter
