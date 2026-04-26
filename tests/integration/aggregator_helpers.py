"""Aggregator helpers used by the generated post.yaml / telemetry.yaml tests.

These thin adapters wrap the real telemetry collectors in
``quonfig.telemetry.collectors`` and shape their output into the
"normalized post body" the cross-SDK YAML spec describes.

The post-body shape differs from the SDK's wire-format payload:

* evaluation_summary  -> a flat list of one dict per (key, conditional value),
  with ``key``, ``type`` (``CONFIG`` / ``FEATURE_FLAG``), ``value`` (resolved),
  ``value_type``, ``count``, ``reason`` (1=STATIC, 2=TARGETING_MATCH,
  3=SPLIT), and a ``summary`` sub-dict with row + weighted-value indices.
* context_shape       -> a list of ``{name, field_types}`` dicts, one per
  namespace.
* example_contexts    -> a single dict of ``namespace -> values`` for the
  first example seen with a ``key`` field present, or ``None`` if none did.

Helpers return ``None`` when the aggregator has nothing to report
(disabled mode, all-log-level evaluations, empty contexts, etc.).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from quonfig.datadir import load_datadir
from quonfig.evaluator import Evaluator
from quonfig.resolver import Resolver
from quonfig.store import ConfigStore
from quonfig.telemetry.collectors import (
    ContextShapeCollector,
    EvaluationSummaryCollector,
    ExampleContextCollector,
)
from quonfig.types import Contexts, EvalResult

DATA_DIR = str(
    Path(__file__).parent.parent.parent.parent
    / "integration-test-data"
    / "data"
    / "integration-tests"
)
ENV_ID = "Production"

# Mirror telemetry_helpers.py — ensure the env vars referenced by the
# integration fixtures are set before any evaluator runs.
os.environ.setdefault(
    "PREFAB_INTEGRATION_TEST_ENCRYPTION_KEY",
    "c87ba22d8662282abe8a0e4651327b579cb64a454ab0f4c170b45b15f049a221",
)
os.environ.setdefault("IS_A_NUMBER", "1234")
os.environ.setdefault("NOT_A_NUMBER", "not_a_number")
os.environ.pop("MISSING_ENV_VAR", None)

_store = ConfigStore()
_store.update(load_datadir(DATA_DIR, ENV_ID))
_evaluator = Evaluator(_store, ENV_ID)
_resolver = Resolver(_store)


# ---------------------------------------------------------------------------
# Public API used by generated tests
# ---------------------------------------------------------------------------


def build_aggregator(kind: str, overrides: Dict[str, Any]):
    """Construct a fresh collector for ``kind`` with the given overrides."""
    if kind == "evaluation_summary":
        enabled = overrides.get("collect_evaluation_summaries", True)
        return EvaluationSummaryCollector(enabled=bool(enabled))

    if kind == "context_shape":
        mode = _normalize_upload_mode(overrides.get("context_upload_mode"))
        return ContextShapeCollector(context_upload_mode=mode)

    if kind == "example_contexts":
        mode = _normalize_upload_mode(overrides.get("context_upload_mode"))
        # rate_limit_ms=0 so the helper records every example we feed in
        # (the cross-SDK suite always expects deterministic dedupe behavior
        # and never relies on the timestamp throttle).
        return ExampleContextCollector(
            context_upload_mode=mode if mode != "shapes_only" else "none",
            rate_limit_ms=0,
        )

    raise ValueError(f"unknown aggregator kind: {kind!r}")


def feed_aggregator(
    agg,
    kind: str,
    data: Any,
    contexts: Optional[Contexts] = None,
) -> None:
    """Feed test data into the aggregator.

    ``data`` shape depends on ``kind``:

    * evaluation_summary -> ``{"keys": [...], "keys_without_context"?: [...]}``
    * context_shape      -> a single context dict, or a list of context dicts
    * example_contexts   -> a single context dict, or a list of context dicts
    """
    contexts = contexts or {}

    if kind == "evaluation_summary":
        if not isinstance(data, dict):
            return
        keys = data.get("keys") or []
        for key in keys:
            result = _evaluate_for_telemetry(key, contexts)
            if result is not None:
                agg.record(result)
        # Some YAML cases use keys_without_context to evaluate the same
        # config under empty context — the resulting counter ends up in a
        # different bucket and exercises rule fallthrough.
        keys_without = data.get("keys_without_context") or []
        for key in keys_without:
            result = _evaluate_for_telemetry(key, {})
            if result is not None:
                agg.record(result)
        return

    if kind == "context_shape":
        for ctx in _iter_context_records(data):
            agg.record(ctx)
        return

    if kind == "example_contexts":
        for ctx in _iter_context_records(data):
            agg.record(ctx)
        return

    raise ValueError(f"unknown aggregator kind: {kind!r}")


def aggregator_post(agg, kind: str, endpoint: str) -> Any:
    """Drain ``agg`` and shape its payload into the YAML-spec post body.

    Returns ``None`` when the aggregator has nothing to send.
    """
    if kind == "evaluation_summary":
        return _post_evaluation_summary(agg)
    if kind == "context_shape":
        return _post_context_shape(agg)
    if kind == "example_contexts":
        return _post_example_contexts(agg)
    raise ValueError(f"unknown aggregator kind: {kind!r}")


# ---------------------------------------------------------------------------
# Evaluation + post helpers
# ---------------------------------------------------------------------------


def _evaluate_for_telemetry(
    key: str, contexts: Optional[Contexts] = None
) -> Optional[EvalResult]:
    ctx = contexts or {}
    result = _evaluator.evaluate(key, ctx)
    if result.reason == "MISSING" or result.value is None:
        return None
    try:
        resolved = _resolver.resolve(result.value, ctx, config_key=key)
    except Exception:
        return None
    result.resolved_value = resolved
    return result


def _post_evaluation_summary(agg: EvaluationSummaryCollector) -> Optional[List[dict]]:
    if not agg.is_enabled():
        return None
    event = agg.drain()
    if event is None or event.summaries is None:
        return None
    summaries = event.summaries.summaries
    if not summaries:
        return None

    # Cross-SDK YAML expects CONFIG rows before FEATURE_FLAG rows; within
    # a type, preserve insertion order (matches the order each key was
    # first seen during feed).
    type_priority = {"config": 0, "feature_flag": 1}
    ordered = sorted(
        enumerate(summaries),
        key=lambda kv: (type_priority.get((kv[1].type or "").lower(), 99), kv[0]),
    )
    rows: List[dict] = []
    for _idx, summary in ordered:
        cfg_type = _config_type_label(summary.type)
        for counter in summary.counters:
            row: Dict[str, Any] = {
                "key": summary.key,
                "type": cfg_type,
                "value": _unwrap_selected_value(counter.selected_value),
                "value_type": _value_type_label(counter.selected_value),
                "count": counter.count,
                "reason": counter.reason,
            }
            summary_block: Dict[str, Any] = {
                "config_row_index": counter.config_row_index,
                "conditional_value_index": counter.conditional_value_index,
            }
            if (
                counter.weighted_value_index is not None
                and counter.weighted_value_index >= 0
            ):
                summary_block["weighted_value_index"] = counter.weighted_value_index
            row["summary"] = summary_block
            rows.append(row)
    return rows


def _post_context_shape(agg: ContextShapeCollector) -> Optional[List[dict]]:
    if not agg.is_enabled():
        return None
    event = agg.drain()
    if event is None or event.context_shapes is None:
        return None
    shapes = event.context_shapes.shapes
    if not shapes:
        return None
    return [{"name": s.name, "field_types": dict(s.field_types)} for s in shapes]


def _post_example_contexts(agg: ExampleContextCollector) -> Optional[Dict[str, Any]]:
    if not agg.is_enabled():
        return None
    event = agg.drain()
    if event is None or event.example_contexts is None:
        return None
    examples = event.example_contexts.examples
    if not examples:
        return None
    # The YAML spec format collapses the (timestamp, context_set) wire
    # representation back into a flat ``namespace -> values`` dict, taking
    # the first example only (dedupe-by-key already happened in the
    # collector).
    first = examples[0]
    out: Dict[str, Any] = {}
    for entry in first.context_set.get("contexts", []):
        if not isinstance(entry, dict):
            continue
        ns = entry.get("type")
        vals = entry.get("values")
        if ns and isinstance(vals, dict):
            out[ns] = dict(vals)
    return out or None


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _iter_context_records(data: Any):
    """Normalize ``data`` into an iterable of context dicts."""
    if data is None:
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(data, dict):
        yield data


def _normalize_upload_mode(value: Optional[Union[str, Any]]) -> str:
    """Map YAML keyword strings (``:none``, ``:shape_only``) onto the
    SDK's internal mode names (``none``, ``shapes_only``)."""
    if value is None:
        return "periodic_example"
    if isinstance(value, bool):
        return "periodic_example"
    s = str(value).lstrip(":").lower()
    if s in ("none", ""):
        return "none"
    if s in ("shape_only", "shapes_only"):
        return "shapes_only"
    if s in ("periodic_example", "periodic"):
        return "periodic_example"
    return s


def _config_type_label(stored_type: str) -> str:
    """Map the SDK's stored config type onto the YAML spec's uppercase form."""
    t = (stored_type or "").lower()
    if t == "feature_flag":
        return "FEATURE_FLAG"
    return "CONFIG"


def _unwrap_selected_value(selected_value: Any) -> Any:
    """Pull the native Python value out of the type-tagged dict the
    collector stores (``{"string": "x"}``, ``{"int": 1}``, etc.)."""
    if not isinstance(selected_value, dict) or not selected_value:
        return selected_value
    # Single-key dict by construction (see marshal_selected_value).
    for v in selected_value.values():
        return v
    return None


def _value_type_label(selected_value: Any) -> str:
    """Map the type-tag from the collector dict onto the YAML spec's
    snake_case value_type label."""
    if isinstance(selected_value, dict) and selected_value:
        tag = next(iter(selected_value.keys()))
        if tag == "stringList":
            return "string_list"
        return tag
    return "string"


__all__ = [
    "build_aggregator",
    "feed_aggregator",
    "aggregator_post",
]
