"""Telemetry transport policy (qfg-y8je.7).

Policy P1-P10: project/plans/2026-09-24-sdk-telemetry-transport-policy.md.
Contract tests T1-T8: integration-test-data/chaos/telemetry-transport-contract.md.
Reference implementation: sdk-node ``src/telemetry/transportQueue.ts``.

This module owns the retained queue of serialized batches, the send gate (30s
floor after a failure + ``Retry-After``), the drain loop, disable-on-auth and
the P7 logging episodes. It knows nothing about collectors or payload shape:
it stores and resends opaque bytes.

Not thread-safe on its own: the reporter runs every method except
``send_final`` under its tick lock, so one POST is in flight at a time (P2).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, List, Optional

from .clock import REAL_CLOCK, TelemetryClock

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelemetryConfig:
    """Resolved transport settings (see the ``telemetry_*`` options on ``Quonfig``)."""

    flush_interval_ms: int = 60_000
    timeout_ms: int = 15_000
    connect_timeout_ms: int = 5_000
    max_retained_batches: int = 5
    max_retained_bytes: int = 2 * 1024 * 1024
    max_retained_age_ms: int = 300_000
    max_evaluation_summaries: int = 10_000
    max_context_shape_fields: int = 10_000
    max_example_contexts: int = 10_000


#: Shipped defaults for the ``telemetry_*`` options (server SDK class).
TELEMETRY_DEFAULTS = TelemetryConfig()

#: No send sooner than this after a failed POST (P4).
RESEND_FLOOR_MS = 30_000
#: ``Retry-After`` is honored up to this (P4).
RETRY_AFTER_CAP_MS = 600_000
#: At most one drop WARN per this interval while dropping continues (P7).
DROP_WARN_INTERVAL_MS = 600_000
#: ``close()`` / interpreter exit gives the live window one POST with this deadline (P8).
SHUTDOWN_FLUSH_DEADLINE_MS = 5_000
#: Bound on the example-context rate-limit map (P6).
EXAMPLE_CONTEXT_SEEN_CAP = 100_000


def classify_status(status: int) -> str:
    """2xx -> ``ok``; 401, 403, 404 -> ``auth``; 408, 429, 5xx -> ``retryable``;
    every other status (other 4xx, 3xx, 1xx) -> ``rejected`` (P3)."""
    if 200 <= status < 300:
        return "ok"
    if status in (401, 403, 404):
        return "auth"
    if status in (408, 429) or 500 <= status < 600:
        return "retryable"
    return "rejected"


_DIGITS = re.compile(r"^\d+$")


def parse_retry_after_ms(header: Optional[str], now_ms: float) -> Optional[float]:
    """Parse a ``Retry-After`` header into a wait in ms: delta-seconds, or an
    HTTP-date relative to ``now_ms`` (past dates -> 0). Unparseable -> ``None``.
    Clamped to :data:`RETRY_AFTER_CAP_MS`."""
    if header is None:
        return None
    v = header.strip()
    if not v:
        return None
    if _DIGITS.match(v):
        ms = float(int(v)) * 1000.0
    else:
        try:
            at = parsedate_to_datetime(v)
        except (TypeError, ValueError, IndexError):
            return None
        if at is None:  # pragma: no cover - older Pythons return None
            return None
        ms = max(0.0, at.timestamp() * 1000.0 - now_ms)
    return min(ms, float(RETRY_AFTER_CAP_MS))


@dataclass
class TelemetryHttpResult:
    """Outcome of one telemetry POST that got an HTTP response."""

    status: int
    retry_after: Optional[str] = None
    body_snippet: str = ""


class TelemetryRequestError(Exception):
    """A telemetry POST that got no HTTP response: the overall/read timeout
    fired (``timeout``), connect or TLS timed out (``connect timeout``), or the
    connection or request failed (``network``)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"telemetry POST {reason}{': ' + detail if detail else ''}")
        self.reason = reason
        self.detail = detail

    def describe(self) -> str:
        """``last_result`` text for the P7 log lines."""
        if self.reason == "network":
            return f"network error: {self.detail}" if self.detail else "network error"
        return self.reason


#: ``send(body, timeout_ms, connect_timeout_ms)``; raises TelemetryRequestError.
TelemetrySend = Callable[[bytes, int, int], TelemetryHttpResult]


@dataclass
class _RetainedBatch:
    body: bytes
    created_at: float
    oversize: bool

    @property
    def size(self) -> int:
        return len(self.body)


class TelemetryTransportQueue:
    def __init__(
        self,
        *,
        send: TelemetrySend,
        telemetry_url: str,
        config: TelemetryConfig,
        on_disabled: Callable[[], None],
        clock: Optional[TelemetryClock] = None,
    ) -> None:
        self._send = send
        self._telemetry_url = telemetry_url
        self._config = config
        self._on_disabled = on_disabled
        self._clock: TelemetryClock = clock or REAL_CLOCK

        self._queue: List[_RetainedBatch] = []
        self._in_flight = False
        self._last_failure_at = -math.inf
        self._retry_after_until = 0.0
        self._disabled = False
        self._stopped = False

        # Outage episode (P7).
        self._failures_since_success = 0
        self._first_failure_at: Optional[float] = None
        self._last_result = ""
        self._last_drop_warn_at: Optional[float] = None
        self._drops_since_warn = 0
        self._drops_this_outage = 0

        # Rejected-batch (other 4xx) cadence.
        self._last_reject_error_at: Optional[float] = None
        self._rejects_since_error = 0

    @property
    def busy(self) -> bool:
        """A POST is in flight."""
        return self._in_flight

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def retained_count(self) -> int:
        """Every queued batch, including a not-yet-sent oversize one."""
        return len(self._queue)

    @property
    def retained_bytes(self) -> int:
        return sum(b.size for b in self._queue)

    def stop(self) -> None:
        """close(): a drain that is mid-POST sends nothing further."""
        self._stopped = True

    def expire(self) -> None:
        """Discard batches older than the max age (strictly greater). Tick step 2."""
        now = self._clock.now_ms()
        # Appended in time order, so only the head can be expired.
        while self._queue and now - self._queue[0].created_at > self._config.max_retained_age_ms:
            self._queue.pop(0)
            minutes = round(self._config.max_retained_age_ms / 60_000)
            self._record_drop(f"batch older than {minutes} min")

    def send_allowed(self) -> bool:
        """The 30s floor after a failure and any Retry-After have both elapsed. Tick step 3."""
        now = self._clock.now_ms()
        return now >= self._last_failure_at + RESEND_FLOOR_MS and now >= self._retry_after_until

    def append(self, body: bytes) -> None:
        """Append a serialized window and enforce the caps (drop oldest). Tick step 4."""
        oversize = len(body) > self._config.max_retained_bytes
        self._queue.append(_RetainedBatch(body, self._clock.now_ms(), oversize))

        kept = [b for b in self._queue if not b.oversize]
        count = len(kept)
        size = sum(b.size for b in kept)
        while count > self._config.max_retained_batches or size > self._config.max_retained_bytes:
            victim = next((b for b in self._queue if not b.oversize), None)
            if victim is None:  # pragma: no cover - count > 0 implies a victim
                break
            self._queue.remove(victim)
            count -= 1
            size -= victim.size
            self._record_drop("retained queue full")

    def drain(self) -> None:
        """POST queued batches oldest-first, one at a time; stop at the first
        failure. Tick step 5."""
        while self._queue and not self._disabled and not self._stopped:
            batch = self._queue[0]
            self._in_flight = True
            try:
                res = self._send(
                    batch.body, self._config.timeout_ms, self._config.connect_timeout_ms
                )
            except TelemetryRequestError as e:
                self._on_retryable_failure(batch, e.describe(), None)
                break
            except Exception as e:  # noqa: BLE001 - anything else is a network failure
                self._on_retryable_failure(batch, f"network error: {e}", None)
                break
            finally:
                self._in_flight = False

            if self._stopped:
                # close() ran while this POST was out; its outcome no longer matters.
                return
            cls = classify_status(res.status)
            if cls == "ok":
                self._queue.pop(0)
                self._on_success()
                continue
            if cls == "retryable":
                self._on_retryable_failure(batch, str(res.status), res.retry_after)
                break
            if cls == "auth":
                self._disable(res.status)
                return
            # rejected: drop this batch, report, carry on with the next one.
            self._queue.pop(0)
            self._on_rejected(res.status, batch.size, res.body_snippet)

        # Oversize batches are never carried across ticks.
        for b in [b for b in self._queue if b.oversize]:
            self._queue.remove(b)
            self._record_drop("batch larger than the byte cap")

    def send_final(self, body: bytes, deadline_ms: int) -> Optional[str]:
        """close(): one POST of the live window bounded by ``deadline_ms``.
        Never retains, never touches the outage episode, never raises. Returns
        the failure text, or ``None`` on a 2xx."""
        try:
            res = self._send(body, deadline_ms, min(self._config.connect_timeout_ms, deadline_ms))
        except TelemetryRequestError as e:
            return e.describe()
        except Exception as e:  # noqa: BLE001
            return f"network error: {e}"
        if classify_status(res.status) != "ok":
            return str(res.status)
        return None

    def log_final_failure(self, result: str, size: int) -> None:
        _LOG.debug(
            "[quonfig] Telemetry final flush at shutdown failed (%s); %d bytes dropped, "
            "%d retained batch(es) abandoned",
            result,
            size,
            self.retained_count,
        )

    # -- episode bookkeeping --------------------------------------------------

    def _on_success(self) -> None:
        if self._failures_since_success == 0:
            return
        now = self._clock.now_ms()
        first = self._first_failure_at if self._first_failure_at is not None else now
        _LOG.info(
            "[quonfig] Telemetry recovered: POST succeeded after %d failed attempt(s) over %ds; "
            "%d batch(es) were dropped.",
            self._failures_since_success,
            round((now - first) / 1000),
            self._drops_this_outage,
        )
        self._failures_since_success = 0
        self._first_failure_at = None
        self._drops_this_outage = 0
        self._last_drop_warn_at = None
        self._drops_since_warn = 0

    def _on_retryable_failure(
        self, batch: _RetainedBatch, result: str, retry_after: Optional[str]
    ) -> None:
        now = self._clock.now_ms()
        self._failures_since_success += 1
        if self._first_failure_at is None:
            self._first_failure_at = now
        self._last_failure_at = now
        self._last_result = result
        wait = parse_retry_after_ms(retry_after, now)
        if wait is not None:
            self._retry_after_until = now + wait

        next_ms = max(self._last_failure_at + RESEND_FLOOR_MS, self._retry_after_until) - now
        _LOG.debug(
            "[quonfig] Telemetry POST failed (%s); %d batch(es) / %d bytes retained, "
            "next send in >= %ds",
            result,
            self.retained_count,
            self.retained_bytes,
            math.ceil(next_ms / 1000),
        )

        if batch.oversize and batch in self._queue:
            self._queue.remove(batch)
            self._record_drop("batch larger than the byte cap")

    def _disable(self, status: int) -> None:
        hint = "wrong telemetry_url" if status == 404 else "the SDK key was rejected"
        _LOG.error(
            "[quonfig] Telemetry disabled for this process: %s answered %d (%s). "
            "Flag evaluation is unaffected.",
            self._telemetry_url,
            status,
            hint,
        )
        self._queue.clear()
        self._disabled = True
        self._on_disabled()

    def _on_rejected(self, status: int, size: int, body_snippet: str) -> None:
        now = self._clock.now_ms()
        if (
            self._last_reject_error_at is None
            or now - self._last_reject_error_at >= DROP_WARN_INTERVAL_MS
        ):
            n = self._rejects_since_error
            more = f", {n} more since the last report" if n > 0 else ""
            _LOG.error(
                "[quonfig] Telemetry batch rejected with %d and dropped (%d bytes%s): %s. "
                "This is likely an SDK bug; please report it.",
                status,
                size,
                more,
                body_snippet,
            )
            self._last_reject_error_at = now
            self._rejects_since_error = 0
        else:
            self._rejects_since_error += 1
            _LOG.debug(
                "[quonfig] Telemetry batch rejected with %d and dropped (%d bytes)", status, size
            )

    def _record_drop(self, reason: str) -> None:
        now = self._clock.now_ms()
        self._drops_since_warn += 1
        self._drops_this_outage += 1
        last_result = self._last_result or "none"
        if self._last_drop_warn_at is None:
            _LOG.warning(
                "[quonfig] Telemetry is dropping data: %s (last POST result: %s). "
                "%d batch(es) dropped so far; retained queue %d/%d batches, %d bytes. "
                "Flag evaluation is unaffected; further drops log at debug with a summary "
                "every 10 min.",
                reason,
                last_result,
                self._drops_this_outage,
                self.retained_count,
                self._config.max_retained_batches,
                self.retained_bytes,
            )
            self._last_drop_warn_at = now
            self._drops_since_warn = 0
        elif now - self._last_drop_warn_at >= DROP_WARN_INTERVAL_MS:
            _LOG.warning(
                "[quonfig] Telemetry still dropping data: %d batch(es) dropped in the last "
                "%d min (last POST result: %s); retained queue %d batches, %d bytes.",
                self._drops_since_warn,
                round((now - self._last_drop_warn_at) / 60_000),
                last_result,
                self.retained_count,
                self.retained_bytes,
            )
            self._last_drop_warn_at = now
            self._drops_since_warn = 0
        else:
            _LOG.debug(
                "[quonfig] Telemetry dropped a batch: %s; %d since the last warning",
                reason,
                self._drops_since_warn,
            )
