from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..reason import field_type_for_value, marshal_selected_value
from ..types import Contexts, EvalResult
from .models import (
    ContextShape,
    ContextShapes,
    EvalSummaries,
    EvaluationCounter,
    EvaluationSummary,
    ExampleContext,
    ExampleContexts,
    TelemetryEvent,
)


def _millis() -> int:
    return int(time.time() * 1000)


class EvaluationSummaryCollector:
    """Aggregates evaluation events into per-key counters with reason + selectedValue."""

    def __init__(self, enabled: bool = True, max_data_size: int = 10_000) -> None:
        self._enabled = enabled
        self._max_data_size = max_data_size
        self._lock = threading.Lock()
        # (config_key, config_type) -> counter_key -> {count, meta}
        self._data: Dict[Tuple, Dict[str, dict]] = defaultdict(dict)
        self._start_millis = _millis()

    def is_enabled(self) -> bool:
        return self._enabled

    def record(self, result: EvalResult) -> None:
        if not self._enabled:
            return
        if result.config_id is None:
            return
        if result.config_type == "log_level":
            return
        if result.resolved_value is None and result.reason in ("MISSING", "ERROR"):
            return

        resolved = result.resolved_value
        selected_value = marshal_selected_value(resolved)

        wv_idx = result.weighted_value_index if result.weighted_value_index >= 0 else None
        counter_key = (
            result.config_id,
            result.row_index or 0,
            wv_idx,
            str(selected_value),
        )
        group_key = (result.config_key, result.config_type)

        with self._lock:
            if len(self._data) >= self._max_data_size and group_key not in self._data:
                return
            group = self._data[group_key]
            ck = str(counter_key)
            if ck not in group:
                group[ck] = {
                    "config_id": result.config_id,
                    "conditional_value_index": result.row_index or 0,
                    "config_row_index": 0,
                    "selected_value": selected_value,
                    "reason": result.telemetry_reason,
                    "weighted_value_index": wv_idx,
                    "count": 0,
                }
            group[ck]["count"] += 1

    def drain(self) -> Optional[TelemetryEvent]:
        with self._lock:
            if not self._data:
                return None
            data = dict(self._data)
            start = self._start_millis
            self._data.clear()
            self._start_millis = _millis()

        end = _millis()
        summaries: List[EvaluationSummary] = []
        for (config_key, config_type), group in data.items():
            counters = [
                EvaluationCounter(
                    config_id=meta["config_id"],
                    conditional_value_index=meta["conditional_value_index"],
                    config_row_index=meta["config_row_index"],
                    selected_value=meta["selected_value"],
                    count=meta["count"],
                    reason=meta["reason"],
                    weighted_value_index=meta["weighted_value_index"],
                )
                for meta in group.values()
            ]
            summaries.append(EvaluationSummary(key=config_key, type=config_type, counters=counters))

        return TelemetryEvent(
            summaries=EvalSummaries(start=start, end=end, summaries=summaries)
        )


class ContextShapeCollector:
    """Tracks field names and their type codes per context namespace."""

    def __init__(
        self, context_upload_mode: str = "shapes_only", max_data_size: int = 10_000
    ) -> None:
        self._enabled = context_upload_mode != "none"
        self._max_data_size = max_data_size
        self._lock = threading.Lock()
        self._shapes: Dict[str, Dict[str, int]] = defaultdict(dict)

    def is_enabled(self) -> bool:
        return self._enabled

    def record(self, contexts: Contexts) -> None:
        if not self._enabled:
            return
        with self._lock:
            for namespace, values in contexts.items():
                if not isinstance(values, dict):
                    continue
                shape = self._shapes[namespace]
                for field_name, value in values.items():
                    if field_name not in shape:
                        shape[field_name] = field_type_for_value(value)

    def drain(self) -> Optional[TelemetryEvent]:
        with self._lock:
            if not self._shapes:
                return None
            shapes = {ns: dict(ft) for ns, ft in self._shapes.items()}
            self._shapes.clear()

        if not shapes:
            return None

        return TelemetryEvent(
            context_shapes=ContextShapes(
                shapes=[ContextShape(name=ns, field_types=ft) for ns, ft in shapes.items()]
            )
        )


class ExampleContextCollector:
    """Collects sampled example contexts, deduplicating by key value."""

    def __init__(
        self,
        context_upload_mode: str = "periodic_example",
        max_data_size: int = 10_000,
        rate_limit_ms: int = 3_600_000,
    ) -> None:
        self._enabled = context_upload_mode == "periodic_example"
        self._max_data_size = max_data_size
        self._rate_limit_ms = rate_limit_ms
        self._lock = threading.Lock()
        self._examples: List[Tuple[int, Contexts]] = []
        self._seen: Dict[str, int] = {}

    def is_enabled(self) -> bool:
        return self._enabled

    def record(self, contexts: Contexts) -> None:
        if not self._enabled:
            return
        key = self._group_key(contexts)
        if not key:
            return
        now = _millis()
        with self._lock:
            if len(self._examples) >= self._max_data_size:
                return
            last_seen = self._seen.get(key)
            if last_seen is not None and now - last_seen < self._rate_limit_ms:
                return
            self._examples.append((now, contexts))
            self._seen[key] = now

    def drain(self) -> Optional[TelemetryEvent]:
        with self._lock:
            if not self._examples:
                return None
            examples = list(self._examples)
            self._examples.clear()
            self._prune_cache()

        result: List[ExampleContext] = []
        for timestamp, contexts in examples:
            context_list = [
                {"type": ns, "values": dict(vals)}
                for ns, vals in contexts.items()
                if isinstance(vals, dict)
            ]
            result.append(
                ExampleContext(
                    timestamp=timestamp,
                    context_set={"contexts": context_list},
                )
            )

        return TelemetryEvent(example_contexts=ExampleContexts(examples=result))

    def _group_key(self, contexts: Contexts) -> str:
        parts = []
        for ctx in contexts.values():
            if not isinstance(ctx, dict):
                continue
            key = ctx.get("key") or ctx.get("tracking_id")
            if key is not None:
                parts.append(str(key))
        return "|".join(sorted(parts))

    def _prune_cache(self) -> None:
        now = _millis()
        self._seen = {k: v for k, v in self._seen.items() if now - v < self._rate_limit_ms}
