from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvaluationCounter:
    config_key: str
    config_id: str
    value_type: str
    row_index: Optional[int]
    count: int = 0


@dataclass
class EvaluationSummary:
    config_key: str
    config_id: str
    value_type: str
    counters: List[EvaluationCounter] = field(default_factory=list)


@dataclass
class ContextShape:
    """Tracks shape (field names) of a context namespace."""
    namespace: str
    field_names: List[str] = field(default_factory=list)


@dataclass
class TelemetryPayload:
    """Payload sent to the telemetry endpoint."""
    evaluation_summaries: List[EvaluationSummary] = field(default_factory=list)
    context_shapes: List[ContextShape] = field(default_factory=list)
    start_millis: int = 0
    end_millis: int = 0

    def to_dict(self) -> dict:
        return {
            "evaluationSummaries": [
                {
                    "key": s.config_key,
                    "configId": s.config_id,
                    "valueType": s.value_type,
                    "counters": [
                        {
                            "configKey": c.config_key,
                            "configId": c.config_id,
                            "rowIndex": c.row_index,
                            "count": c.count,
                        }
                        for c in s.counters
                    ],
                }
                for s in self.evaluation_summaries
            ],
            "contextShapes": [
                {
                    "namespace": cs.namespace,
                    "fieldNames": cs.field_names,
                }
                for cs in self.context_shapes
            ],
            "start": self.start_millis,
            "end": self.end_millis,
        }
