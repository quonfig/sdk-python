from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from ..types import Contexts, EvalResult
from .models import ContextShape, EvaluationCounter, EvaluationSummary


def _current_time_millis() -> int:
    return int(time.time() * 1000)


class EvaluationSummaryCollector:
    """Collects evaluation events and aggregates them into summaries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (config_id, value_type, row_index) -> count
        self._counts: Dict[Tuple, int] = defaultdict(int)
        self._start_millis = _current_time_millis()

    def record(self, result: EvalResult) -> None:
        if result.config_id is None:
            return
        key = (
            result.config_key,
            result.config_id,
            result.value_type,
            result.row_index,
        )
        with self._lock:
            self._counts[key] += 1

    def flush(self) -> Tuple[List[EvaluationSummary], int, int]:
        """
        Returns (summaries, start_millis, end_millis) and resets the collector.
        """
        end_millis = _current_time_millis()
        with self._lock:
            counts = dict(self._counts)
            start = self._start_millis
            self._counts.clear()
            self._start_millis = end_millis

        # Group by config_key/config_id/value_type
        groups: Dict[Tuple, List[EvaluationCounter]] = defaultdict(list)
        for (config_key, config_id, value_type, row_index), count in counts.items():
            group_key = (config_key, config_id, value_type)
            groups[group_key].append(
                EvaluationCounter(
                    config_key=config_key,
                    config_id=config_id,
                    value_type=value_type,
                    row_index=row_index,
                    count=count,
                )
            )

        summaries = [
            EvaluationSummary(
                config_key=gk[0],
                config_id=gk[1],
                value_type=gk[2],
                counters=counters,
            )
            for gk, counters in groups.items()
        ]
        return summaries, start, end_millis


class ContextShapeCollector:
    """Tracks unique field names seen per context namespace."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # namespace -> set of field names
        self._shapes: Dict[str, set] = defaultdict(set)

    def record(self, contexts: Contexts) -> None:
        with self._lock:
            for namespace, values in contexts.items():
                if isinstance(values, dict):
                    self._shapes[namespace].update(values.keys())

    def flush(self) -> List[ContextShape]:
        with self._lock:
            shapes = [
                ContextShape(namespace=ns, field_names=sorted(fields))
                for ns, fields in self._shapes.items()
            ]
            self._shapes.clear()
        return shapes
