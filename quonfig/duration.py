"""ISO-8601 duration grammar shared across Quonfig (qfg-2agi decision 1).

Fractions are allowed on seconds only; at least one component; no dangling
``T``; at most 9 fractional digits; magnitude at most ``P36500D``.
Milliseconds use exact decimal arithmetic, rounded half up (decision 2).
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

# Matched with ``fullmatch`` (``$`` would accept a trailing newline) and
# ``[0-9]`` (``\d`` matches non-ASCII digits such as U+0665).
_DURATION_RE = re.compile(
    r"P(?:(?P<d>[0-9]+)D)?"
    r"(?:(?P<t>T)(?:(?P<h>[0-9]+)H)?(?:(?P<m>[0-9]+)M)?(?:(?P<s>[0-9]+(?:\.(?P<frac>[0-9]+))?)S)?)?"
)

_MAX_MS = 36500 * 86_400_000


def parse_duration_millis(text: str) -> Optional[int]:
    """Return the duration in whole milliseconds, or None if ``text`` is invalid."""
    if not isinstance(text, str):
        return None
    m = _DURATION_RE.fullmatch(text)
    if m is None:
        return None
    d, h, mi, s = m.group("d"), m.group("h"), m.group("m"), m.group("s")
    if d is None and h is None and mi is None and s is None:
        return None  # "P" / "PT": no component
    if m.group("t") is not None and h is None and mi is None and s is None:
        return None  # dangling T, e.g. "P1DT"
    frac = m.group("frac")
    if frac is not None and len(frac) > 9:
        return None
    total = (
        Decimal(d or 0) * 86_400_000
        + Decimal(h or 0) * 3_600_000
        + Decimal(mi or 0) * 60_000
        + Decimal(s or 0) * 1000
    )
    ms = int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if ms > _MAX_MS:
        return None
    return ms
