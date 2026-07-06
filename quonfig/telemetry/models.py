from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvaluationCounter:
    config_id: str
    conditional_value_index: int
    config_row_index: int
    selected_value: Any  # {"string": "..."}, {"bool": ...}, etc.
    count: int
    reason: int
    weighted_value_index: Optional[int] = None
    # ``selected_value`` is what goes on the wire — for confidential or
    # encrypted values that's the redacted ``{"string": "*****abc12"}`` form.
    # ``display_value`` keeps the original (resolved/decrypted) marshaled
    # value so post-body shapers can surface the unredacted ``value`` /
    # ``value_type`` fields alongside the redacted wire ``selected_value``.
    # Equal to ``selected_value`` when no redaction was applied.
    display_value: Any = None


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
    context_set: Dict[str, Any]  # {"contexts": [{"type": "user", "values": {...}}]}


@dataclass
class ExampleContexts:
    examples: List[ExampleContext] = field(default_factory=list)


@dataclass
class Failover:
    """Per-flush-window failover counters (qfg-41nh.18).

    Additive on the wire — an older api-telemetry strips the unknown field — and
    only sent when at least one counter is non-zero, so a healthy client emits
    nothing. ``start``/``end`` are unix MILLISECONDS, matching the eval-summary
    window convention. Mirrors sdk-go's ``FailoverEvent``.
    """

    start: int
    end: int
    hedge_fired: int
    guard_rejected: int
    resolved_from_primary: int
    resolved_from_secondary: int
    resolved_from_lkg: int


@dataclass
class TelemetryEvent:
    summaries: Optional[EvalSummaries] = None
    context_shapes: Optional[ContextShapes] = None
    example_contexts: Optional[ExampleContexts] = None
    failover: Optional[Failover] = None

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
        if self.failover:
            # camelCase EXACTLY as api-telemetry's Zod schema + ClickHouse MV
            # parse them, matching sdk-go's FailoverEvent JSON tags.
            result["failover"] = {
                "start": self.failover.start,
                "end": self.failover.end,
                "hedgeFired": self.failover.hedge_fired,
                "guardRejected": self.failover.guard_rejected,
                "resolvedFromPrimary": self.failover.resolved_from_primary,
                "resolvedFromSecondary": self.failover.resolved_from_secondary,
                "resolvedFromLkg": self.failover.resolved_from_lkg,
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
