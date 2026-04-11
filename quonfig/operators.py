from __future__ import annotations

import re
from datetime import datetime, date, timezone
from typing import Any, Callable, Optional, TYPE_CHECKING

from .types import Contexts
from .context import get_context_value

if TYPE_CHECKING:
    from .store import ConfigStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> Optional[float]:
    """Convert to float, accepting numbers and numeric strings."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_float_strict(v: Any) -> Optional[float]:
    """Convert to float, but reject strings (context values must be actual numbers)."""
    if isinstance(v, str):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ensure_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    return [value]


# ---------------------------------------------------------------------------
# Individual operator functions
# Signature: (prop_value, criterion_value, contexts, store) -> bool
# ---------------------------------------------------------------------------

def always_true(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    return True


def not_set(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    return prop_value is None


def prop_is_one_of(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    criterion_values = _ensure_list(criterion_value)
    prop_values = _ensure_list(prop_value)
    return any(str(v1) == str(v2) for v1 in criterion_values for v2 in prop_values)


def prop_is_not_one_of(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    return not prop_is_one_of(prop_value, criterion_value, contexts, store)


def prop_ends_with_one_of(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    if not isinstance(prop_value, str):
        return False
    criterion_values = _ensure_list(criterion_value)
    return any(prop_value.endswith(str(v)) for v in criterion_values)


def prop_starts_with_one_of(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    if not isinstance(prop_value, str):
        return False
    criterion_values = _ensure_list(criterion_value)
    return any(prop_value.startswith(str(v)) for v in criterion_values)


def prop_contains_one_of(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    if not isinstance(prop_value, str):
        return False
    criterion_values = _ensure_list(criterion_value)
    return any(str(v) in prop_value for v in criterion_values)


def prop_matches(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    if not isinstance(prop_value, str) or not isinstance(criterion_value, str):
        return False
    try:
        pattern = re.compile(criterion_value)
        return bool(pattern.search(prop_value))
    except (re.error, TypeError):
        return False


def prop_does_not_match(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    if not isinstance(prop_value, str) or not isinstance(criterion_value, str):
        return False
    try:
        pattern = re.compile(criterion_value)
        return not bool(pattern.search(prop_value))
    except (re.error, TypeError):
        return False


def prop_less_than(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    # prop_value (from context) must be a real number, not a string
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a < b


def prop_greater_than(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a > b


def prop_less_than_or_equal(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a <= b


def prop_greater_than_or_equal(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a >= b


def in_int_range(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    """
    criterion_value should be a dict with "start" and "end" (inclusive).
    Or a list [start, end].
    """
    a = _to_float(prop_value)
    if a is None:
        return False
    try:
        if isinstance(criterion_value, dict):
            start = float(criterion_value.get("start", criterion_value.get("low", 0)))
            end = float(criterion_value.get("end", criterion_value.get("high", 0)))
        elif isinstance(criterion_value, (list, tuple)) and len(criterion_value) >= 2:
            start, end = float(criterion_value[0]), float(criterion_value[1])
        else:
            return False
        return start <= a <= end
    except (TypeError, ValueError):
        return False


def in_seg(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    """Look up segment by name and evaluate it against the current contexts."""
    if not isinstance(criterion_value, str):
        return False
    seg_config = store.get(criterion_value)
    if seg_config is None:
        return False
    # Avoid circular segment references by importing lazily
    from .evaluator import Evaluator
    # Use a minimal evaluator with no environment (segment configs don't use env)
    evaluator = Evaluator(store, environment_id="")
    result = evaluator.evaluate(criterion_value, contexts)
    if result.reason == "MISSING":
        return False
    # Segment value should be a bool
    v = result.value
    if v is None:
        return False
    # The value from the rule is a Value object; we need its actual .value
    if hasattr(v, "value"):
        return bool(v.value)
    return bool(v)


def not_in_seg(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    return not in_seg(prop_value, criterion_value, contexts, store)


_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def _parse_semver(v: Any) -> Optional[tuple]:
    """Parse a strict semver string (major.minor.patch) into a comparable tuple.
    Returns None if invalid."""
    if not isinstance(v, str):
        return None
    m = _SEMVER_PATTERN.match(v)
    if not m:
        return None
    major = int(m.group("major"))
    minor = int(m.group("minor"))
    patch = int(m.group("patch"))
    return (major, minor, patch)


def prop_semver_less_than(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    a = _parse_semver(prop_value)
    b = _parse_semver(criterion_value)
    if a is None or b is None:
        return False
    return a < b


def prop_semver_equal(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    a = _parse_semver(prop_value)
    b = _parse_semver(criterion_value)
    if a is None or b is None:
        return False
    return a == b


def prop_semver_greater_than(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    a = _parse_semver(prop_value)
    b = _parse_semver(criterion_value)
    if a is None or b is None:
        return False
    return a > b


def _to_millis(value: Any) -> Optional[int]:
    """Convert various date/time representations to milliseconds since epoch."""
    try:
        if isinstance(value, str):
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            return int(dt.timestamp() * 1000)
        elif isinstance(value, datetime):
            return int(value.timestamp() * 1000)
        elif isinstance(value, date):
            dt = datetime.combine(value, datetime.min.time(), timezone.utc)
            return int(dt.timestamp() * 1000)
        else:
            return int(float(value))
    except (ValueError, TypeError, OSError):
        return None


def prop_before(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    context_millis = _to_millis(prop_value)
    if context_millis is None:
        return False
    try:
        criterion_millis = int(float(criterion_value))
    except (TypeError, ValueError):
        return False
    return context_millis < criterion_millis


def prop_after(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    context_millis = _to_millis(prop_value)
    if context_millis is None:
        return False
    try:
        criterion_millis = int(float(criterion_value))
    except (TypeError, ValueError):
        return False
    return context_millis > criterion_millis


def hierarchical_match(prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore") -> bool:
    if not isinstance(prop_value, str):
        return False
    if not isinstance(criterion_value, str):
        return False
    return prop_value.startswith(criterion_value)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

OPERATOR_DISPATCH: dict = {
    "ALWAYS_TRUE": always_true,
    "NOT_SET": not_set,
    "PROP_IS_ONE_OF": prop_is_one_of,
    "LOOKUP_KEY_IN": prop_is_one_of,
    "PROP_IS_NOT_ONE_OF": prop_is_not_one_of,
    "LOOKUP_KEY_NOT_IN": prop_is_not_one_of,
    "PROP_ENDS_WITH_ONE_OF": prop_ends_with_one_of,
    "PROP_DOES_NOT_END_WITH_ONE_OF": lambda pv, cv, ctx, s: not prop_ends_with_one_of(pv, cv, ctx, s),
    "PROP_STARTS_WITH_ONE_OF": prop_starts_with_one_of,
    "PROP_DOES_NOT_START_WITH_ONE_OF": lambda pv, cv, ctx, s: not prop_starts_with_one_of(pv, cv, ctx, s),
    "PROP_CONTAINS_ONE_OF": prop_contains_one_of,
    "PROP_DOES_NOT_CONTAIN_ONE_OF": lambda pv, cv, ctx, s: not prop_contains_one_of(pv, cv, ctx, s),
    "PROP_MATCHES": prop_matches,
    "PROP_DOES_NOT_MATCH": prop_does_not_match,
    "PROP_LESS_THAN": prop_less_than,
    "PROP_GREATER_THAN": prop_greater_than,
    "PROP_LESS_THAN_OR_EQUAL": prop_less_than_or_equal,
    "PROP_GREATER_THAN_OR_EQUAL": prop_greater_than_or_equal,
    "IN_INT_RANGE": in_int_range,
    "IN_SEG": in_seg,
    "NOT_IN_SEG": not_in_seg,
    "PROP_SEMVER_LESS_THAN": prop_semver_less_than,
    "PROP_SEMVER_EQUAL": prop_semver_equal,
    "PROP_SEMVER_GREATER_THAN": prop_semver_greater_than,
    "PROP_BEFORE": prop_before,
    "PROP_AFTER": prop_after,
    "HIERARCHICAL_MATCH": hierarchical_match,
}


def evaluate_operator(
    operator: str,
    prop_value: Any,
    criterion_value: Any,
    contexts: Contexts,
    store: "ConfigStore",
) -> bool:
    fn: Optional[Callable] = OPERATOR_DISPATCH.get(operator)
    if fn is None:
        return False
    try:
        return bool(fn(prop_value, criterion_value, contexts, store))
    except Exception:
        return False
