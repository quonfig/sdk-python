"""Time source for the telemetry transport (qfg-y8je.7).

The tick timer, the 30s resend floor, ``Retry-After`` and retained-batch age
all read it. Production uses :data:`REAL_CLOCK`; the transport contract tests
inject a manual clock (``Quonfig._telemetry_clock``, a private test seam).
"""

from __future__ import annotations

import threading
import time
from typing import Protocol


class TelemetryClock(Protocol):
    def now_ms(self) -> float:
        """Wall-clock time in epoch milliseconds (``Retry-After`` HTTP-dates
        are compared against it)."""
        ...

    def wait(self, event: threading.Event, timeout_s: float) -> bool:
        """Block until ``event`` is set or ``timeout_s`` passes; return
        whether the event is set. The reporter's tick loop sleeps here."""
        ...


class _RealClock:
    def now_ms(self) -> float:
        return time.time() * 1000.0

    def wait(self, event: threading.Event, timeout_s: float) -> bool:
        return event.wait(timeout_s)


REAL_CLOCK: TelemetryClock = _RealClock()
