from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional, Tuple

from .types import Contexts

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


_MAX_SAFE_INTEGER = 2**53


def _canonical_str(v: Any) -> str:
    """The one cross-SDK string form of a value (qfg-goi1.5 R1).

    Strings are used as-is; bools are ``"true"``/``"false"``; a number whose
    value is integral and below 2^53 in magnitude is plain base-10 digits
    whatever its runtime type (so ``1.0`` -> ``"1"`` and ``1234567.0`` ->
    ``"1234567"``, never ``"1.0"`` or an exponent form); other finite floats
    use Python's shortest round-trip repr (``1.5`` -> ``"1.5"``).
    """
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isfinite(v) and v.is_integer() and abs(v) < _MAX_SAFE_INTEGER:
            return str(int(v))
        return repr(v)
    return str(v)


def _string_operand(v: Any) -> Optional[str]:
    """Context value for the string operators (R2): strings, bools and
    numbers are stringified with :func:`_canonical_str`; anything else
    (missing, lists, dicts) cannot match."""
    if isinstance(v, (str, bool, int, float)):
        return _canonical_str(v)
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


def always_true(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    return True


def not_set(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    return prop_value is None


def prop_is_one_of(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    criterion_values = _ensure_list(criterion_value)
    prop_values = _ensure_list(prop_value)
    criterion_strings = {_canonical_str(v) for v in criterion_values}
    return any(_canonical_str(v) in criterion_strings for v in prop_values)


def prop_is_not_one_of(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    return not prop_is_one_of(prop_value, criterion_value, contexts, store)


def prop_ends_with_one_of(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    s = _string_operand(prop_value)
    if s is None:
        return False
    criterion_values = _ensure_list(criterion_value)
    return any(s.endswith(_canonical_str(v)) for v in criterion_values)


def prop_starts_with_one_of(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    s = _string_operand(prop_value)
    if s is None:
        return False
    criterion_values = _ensure_list(criterion_value)
    return any(s.startswith(_canonical_str(v)) for v in criterion_values)


def prop_contains_one_of(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    s = _string_operand(prop_value)
    if s is None:
        return False
    criterion_values = _ensure_list(criterion_value)
    return any(_canonical_str(v) in s for v in criterion_values)


def prop_matches(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    if not isinstance(prop_value, str) or not isinstance(criterion_value, str):
        return False
    try:
        pattern = re.compile(criterion_value)
        return bool(pattern.search(prop_value))
    except (re.error, TypeError):
        return False


def prop_does_not_match(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    if not isinstance(prop_value, str) or not isinstance(criterion_value, str):
        return False
    try:
        pattern = re.compile(criterion_value)
        return not bool(pattern.search(prop_value))
    except (re.error, TypeError):
        return False


def prop_less_than(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    # prop_value (from context) must be a real number, not a string
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a < b


def prop_greater_than(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a > b


def prop_less_than_or_equal(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a <= b


def prop_greater_than_or_equal(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    a = _to_float_strict(prop_value)
    b = _to_float(criterion_value)
    if a is None or b is None:
        return False
    return a >= b


def in_int_range(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    """
    criterion_value is ``{"start": int, "end": int}``: the range is
    ``[start, end)`` (start inclusive, end exclusive) and a missing bound is
    unbounded (qfg-goi1.5 R3, the Prefab proto definition).
    """
    if isinstance(prop_value, bool):
        return False
    a = _to_float(prop_value)
    if a is None or not isinstance(criterion_value, dict):
        return False
    try:
        start = criterion_value.get("start")
        end = criterion_value.get("end")
        if start is not None and a < float(start):
            return False
        if end is not None and a >= float(end):
            return False
        return True
    except (TypeError, ValueError):
        return False


def in_seg(
    prop_value: Any,
    criterion_value: Any,
    contexts: Contexts,
    store: "ConfigStore",
    seg_path: Tuple[str, ...] = (),
) -> bool:
    """Look up segment by name and evaluate it against the current contexts.

    ``seg_path`` holds the keys of the configs currently being evaluated. A
    reference back onto that path is a cycle and is treated like a missing
    segment (IN_SEG false, NOT_IN_SEG true), matching sdk-go (qfg-9dxb.7).
    """
    if not isinstance(criterion_value, str):
        return False
    if criterion_value in seg_path:
        return False
    seg_config = store.get(criterion_value)
    if seg_config is None:
        return False
    # Avoid circular segment references by importing lazily
    from .evaluator import Evaluator

    # Use a minimal evaluator with no environment (segment configs don't use env)
    evaluator = Evaluator(store, environment_id="")
    result = evaluator.evaluate(criterion_value, contexts, _seg_path=seg_path)
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


def not_in_seg(
    prop_value: Any,
    criterion_value: Any,
    contexts: Contexts,
    store: "ConfigStore",
    seg_path: Tuple[str, ...] = (),
) -> bool:
    return not in_seg(prop_value, criterion_value, contexts, store, seg_path)


_SEMVER_NUM = r"(?:0|[1-9]\d*)"
_SEMVER_PRE_ID = r"(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
_SEMVER_BUILD_ID = r"[0-9a-zA-Z-]+"
_SEMVER_PATTERN = re.compile(
    rf"^(?P<major>{_SEMVER_NUM})"
    rf"(?:\.(?P<minor>{_SEMVER_NUM})"
    rf"(?:\.(?P<patch>{_SEMVER_NUM})"
    rf"(?:-(?P<prerelease>{_SEMVER_PRE_ID}(?:\.{_SEMVER_PRE_ID})*))?"
    rf"(?:\+(?P<buildmetadata>{_SEMVER_BUILD_ID}(?:\.{_SEMVER_BUILD_ID})*))?"
    r")?)?$",
    re.ASCII,
)


class _SemVer:
    """A parsed version ordered by SemVer 2.0 precedence (qfg-goi1.5 R4):
    a prerelease sorts before its release, prerelease identifiers compare
    numerically when all-digit and lexically otherwise, and build metadata
    is ignored."""

    __slots__ = ("core", "prerelease")

    def __init__(self, core: Tuple[int, int, int], prerelease: Tuple[str, ...]):
        self.core = core
        self.prerelease = prerelease

    def compare(self, other: "_SemVer") -> int:
        if self.core != other.core:
            return -1 if self.core < other.core else 1
        a, b = self.prerelease, other.prerelease
        if a == b:
            return 0
        if not a:
            return 1
        if not b:
            return -1
        for x, y in zip(a, b):
            if x == y:
                continue
            x_num, y_num = x.isdigit(), y.isdigit()
            if x_num and y_num:
                return -1 if int(x) < int(y) else 1
            if x_num:
                return -1
            if y_num:
                return 1
            return -1 if x < y else 1
        return -1 if len(a) < len(b) else 1


def _parse_semver(v: Any) -> Optional[_SemVer]:
    """Parse a SemVer 2.0 string, or the abbreviated ``"x"`` / ``"x.y"``
    forms read as ``x.0.0`` / ``x.y.0`` (qfg-goi1.5 Q5). A ``v`` prefix is
    not a version. Returns None if invalid."""
    if not isinstance(v, str):
        return None
    m = _SEMVER_PATTERN.fullmatch(v)
    if not m:
        return None
    core = (int(m.group("major")), int(m.group("minor") or 0), int(m.group("patch") or 0))
    pre = m.group("prerelease")
    return _SemVer(core, tuple(pre.split(".")) if pre else ())


def prop_semver_less_than(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    a = _parse_semver(prop_value)
    b = _parse_semver(criterion_value)
    if a is None or b is None:
        return False
    return a.compare(b) < 0


def prop_semver_equal(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    a = _parse_semver(prop_value)
    b = _parse_semver(criterion_value)
    if a is None or b is None:
        return False
    return a.compare(b) == 0


def prop_semver_greater_than(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    a = _parse_semver(prop_value)
    b = _parse_semver(criterion_value)
    if a is None or b is None:
        return False
    return a.compare(b) > 0


_DIGITS_PATTERN = re.compile(r"^-?[0-9]+$", re.ASCII)
_RFC3339_PATTERN = re.compile(
    r"^(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})"
    r"T(?P<h>[0-9]{2}):(?P<mi>[0-9]{2}):(?P<s>[0-9]{2})(?:\.(?P<frac>[0-9]+))?"
    r"(?:(?P<z>Z)|(?P<sign>[+-])(?P<oh>[0-9]{2}):(?P<om>[0-9]{2}))$",
    re.ASCII,
)


def _rfc3339_to_millis(value: str) -> Optional[int]:
    m = _RFC3339_PATTERN.fullmatch(value)
    if not m:
        return None
    if m.group("z"):
        tz = timezone.utc
    else:
        oh, om = int(m.group("oh")), int(m.group("om"))
        if oh > 23 or om > 59:
            return None
        offset = timedelta(hours=oh, minutes=om)
        tz = timezone(-offset if m.group("sign") == "-" else offset)
    frac = (m.group("frac") or "")[:6].ljust(6, "0")
    try:
        dt = datetime(
            int(m.group("y")),
            int(m.group("mo")),
            int(m.group("d")),
            int(m.group("h")),
            int(m.group("mi")),
            int(m.group("s")),
            int(frac),
            tzinfo=tz,
        )
    except ValueError:
        return None
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _to_millis(value: Any) -> Optional[int]:
    """Read a timestamp as milliseconds since the epoch (qfg-goi1.5 R5).

    The same grammar applies to the context value and the criterion: a
    number (epoch ms, truncated), an all-digit string (epoch ms), or an
    RFC3339 string with ``Z`` or a numeric offset (fractional seconds
    allowed). Date-only, offset-less and free-form strings are not
    timestamps. Python ``datetime``/``date`` objects keep their historical
    handling (a tz-naive datetime is host local time; a date is UTC
    midnight).
    """
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            if _DIGITS_PATTERN.fullmatch(value):
                return int(value)
            return _rfc3339_to_millis(value)
        if isinstance(value, datetime):
            return int(value.timestamp() * 1000)
        if isinstance(value, date):
            dt = datetime.combine(value, datetime.min.time(), timezone.utc)
            return int(dt.timestamp() * 1000)
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return int(value)
        return None
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def prop_before(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    context_millis = _to_millis(prop_value)
    criterion_millis = _to_millis(criterion_value)
    if context_millis is None or criterion_millis is None:
        return False
    return context_millis < criterion_millis


def prop_after(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    context_millis = _to_millis(prop_value)
    criterion_millis = _to_millis(criterion_value)
    if context_millis is None or criterion_millis is None:
        return False
    return context_millis > criterion_millis


def hierarchical_match(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    s = _string_operand(prop_value)
    if s is None or not isinstance(criterion_value, str):
        return False
    return s.startswith(criterion_value)


def is_present(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    # Note: the dispatch-table fallback only sees the resolved value and
    # cannot tell apart "missing" from "explicitly None". The authoritative
    # implementation lives in Evaluator._criterion_matches which has access
    # to the dotted-path `found` flag. Empty string, 0 and False are present;
    # only None counts as absent here. Using `is not None` deliberately —
    # `if prop_value:` would falsely treat "" / 0 / False as absent.
    return prop_value is not None


def is_not_present(
    prop_value: Any, criterion_value: Any, contexts: Contexts, store: "ConfigStore"
) -> bool:
    return prop_value is None


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
    "PROP_DOES_NOT_END_WITH_ONE_OF": lambda pv, cv, ctx, s: (
        not prop_ends_with_one_of(pv, cv, ctx, s)
    ),
    "PROP_STARTS_WITH_ONE_OF": prop_starts_with_one_of,
    "PROP_DOES_NOT_START_WITH_ONE_OF": lambda pv, cv, ctx, s: (
        not prop_starts_with_one_of(pv, cv, ctx, s)
    ),
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
    "IS_PRESENT": is_present,
    "IS_NOT_PRESENT": is_not_present,
}


def evaluate_operator(
    operator: str,
    prop_value: Any,
    criterion_value: Any,
    contexts: Contexts,
    store: "ConfigStore",
    seg_path: Tuple[str, ...] = (),
) -> bool:
    fn: Optional[Callable] = OPERATOR_DISPATCH.get(operator)
    if fn is None:
        return False
    try:
        if operator in ("IN_SEG", "NOT_IN_SEG"):
            return bool(fn(prop_value, criterion_value, contexts, store, seg_path))
        return bool(fn(prop_value, criterion_value, contexts, store))
    except Exception:
        return False
