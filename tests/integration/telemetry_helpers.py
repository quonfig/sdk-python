"""Shared helpers for telemetry integration tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

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


def evaluate_for_telemetry(key: str, contexts: Optional[Contexts] = None) -> Optional[EvalResult]:
    """Evaluate a key and return a fully-populated EvalResult ready for the aggregator."""
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


# Re-export collectors for convenience
__all__ = [
    "evaluate_for_telemetry",
    "EvaluationSummaryCollector",
    "ContextShapeCollector",
    "ExampleContextCollector",
]
