"""Per-tick expectation timeline shared by both chaos runners (qfg-goi1.7).

Pure (no clock, no probe): the runners pass in the elapsed time and an
``evaluate`` callable, so the rules in integration-test-data
``chaos/README.md`` "Timeline rules (every harness)" are unit-testable in
``tests/unit/test_chaos_timeline.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

# (status, reason, skipped_leaves); status is "pass" | "fail" | "skip".
EvalFn = Callable[[], Tuple[str, str, List[str]]]


@dataclass
class ExpState:
    idx: int
    exp: Dict[str, Any]
    passed: bool = False
    failed: bool = False
    skipped: bool = False
    hit_at: Any = None
    held_since: Any = None
    passed_at: Any = None
    last_reason: str = ""
    skipped_leaves: List[str] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.passed or self.failed or self.skipped


ALLOWED_EXPECTATION_KEYS = frozenset({"within_ms", "assert", "must_hold_for_ms", "after_ms"})


def step(s: ExpState, elapsed_ms: float, evaluate: EvalFn, skip_shape: bool = False) -> None:
    """Advance one expectation by one tick at ``elapsed_ms`` from t=0.

    - ``skip_shape``: the expression is a whole-expression ``server_metric``
      (SKIPPED by shape, not time), so it is terminal SKIPPED regardless of
      ``after_ms``.
    - Rule 1: while ``elapsed_ms < after_ms`` the expectation is not evaluated
      at all (no PASS, no hold start, not terminal).
    - Otherwise: the first true sample starts the hold, a false sample resets
      it, PASS once the hold completes, FAIL past ``within_ms``.
    """
    if s.terminal:
        return
    if skip_shape:
        status, reason, skipped = evaluate()
        s.last_reason = reason
        s.skipped_leaves = skipped
        s.skipped = True
        return
    if elapsed_ms < int(s.exp.get("after_ms") or 0):
        return
    status, reason, skipped = evaluate()
    s.last_reason = reason
    s.skipped_leaves = skipped
    if status == "skip":
        s.skipped = True
        return
    if status == "pass":
        if s.held_since is None:
            s.held_since = elapsed_ms
            s.hit_at = elapsed_ms
        hold_for = int(s.exp.get("must_hold_for_ms") or 0)
        if hold_for <= 0 or (elapsed_ms - s.held_since) >= hold_for:
            s.passed = True
            s.passed_at = elapsed_ms
    else:
        s.held_since = None
    if not s.passed and elapsed_ms > int(s.exp["within_ms"]):
        s.failed = True


def validate_expectations(run: Dict[str, Any], source: str) -> None:
    """Rule 4: reject any expectation key outside the schema, naming it.

    A harness pinned to a newer corpus than it implements must fail loudly
    rather than silently drop a timing field.
    """
    for i, exp in enumerate(run.get("expectations") or []):
        unknown = sorted(set(exp) - ALLOWED_EXPECTATION_KEYS)
        if unknown:
            raise ValueError(
                f"{source}: test {run.get('name')!r} expectation[{i}] has unknown key(s) "
                f"{', '.join(unknown)} (allowed: {', '.join(sorted(ALLOWED_EXPECTATION_KEYS))})"
            )
