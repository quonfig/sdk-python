from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvaluationCounter:
    config_id: str
    conditional_value_index: int
    config_row_index: int
    selected_value: Any          # {"string": "..."}, {"bool": ...}, etc.
    count: int
    reason: int
    weighted_value_index: Optional[int] = None


@dataclass
class EvaluationSummary:
    key: str
    type: str
    counters: List[EvaluationCounter] = field(default_factory=list)


@dataclass
class EvalSummaries:
    start: int
    end: int
    summaries: List[EvaluationSummary] = field(default_factory=list)


@dataclass
class ContextShape:
    name: str
    field_types: Dict[str, int] = field(default_factory=dict)


@dataclass
class ContextShapes:
    shapes: List[ContextShape] = field(default_factory=list)


@dataclass
class ExampleContext:
    timestamp: int
    context_set: Dict[str, Any]   # {"contexts": [{"type": "user", "values": {...}}]}


@dataclass
class ExampleContexts:
    examples: List[ExampleContext] = field(default_factory=list)


@dataclass
class TelemetryEvent:
    summaries: Optional[EvalSummaries] = None
    context_shapes: Optional[ContextShapes] = None
    example_contexts: Optional[ExampleContexts] = None

    def to_dict(self) -> dict:
        result: dict = {}
        if self.summaries:
            result["summaries"] = {
                "start": self.summaries.start,
                "end": self.summaries.end,
                "summaries": [
                    {
                        "key": s.key,
                        "type": s.type,
                        "counters": [_counter_to_dict(c) for c in s.counters],
                    }
                    for s in self.summaries.summaries
                ],
            }
        if self.context_shapes:
            result["contextShapes"] = {
                "shapes": [
                    {"name": s.name, "fieldTypes": s.field_types}
                    for s in self.context_shapes.shapes
                ]
            }
        if self.example_contexts:
            result["exampleContexts"] = {
                "examples": [
                    {
                        "timestamp": e.timestamp,
                        "contextSet": e.context_set,
                    }
                    for e in self.example_contexts.examples
                ]
            }
        return result


def _counter_to_dict(c: EvaluationCounter) -> dict:
    d: dict = {
        "configId": c.config_id,
        "conditionalValueIndex": c.conditional_value_index,
        "configRowIndex": c.config_row_index,
        "selectedValue": c.selected_value,
        "count": c.count,
        "reason": c.reason,
    }
    if c.weighted_value_index is not None and c.weighted_value_index >= 0:
        d["weightedValueIndex"] = c.weighted_value_index
    return d


@dataclass
class TelemetryPayload:
    instance_hash: str
    events: List[TelemetryEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instanceHash": self.instance_hash,
            "events": [e.to_dict() for e in self.events],
        }
