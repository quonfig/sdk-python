from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import threading
import time
import weakref
from typing import Any, Dict, List, Optional

import requests  # type: ignore[import-untyped]

from ..transport import QUONFIG_VERSION
from ..types import Contexts, EvalResult
from .clock import REAL_CLOCK, TelemetryClock
from .collectors import (
    ContextShapeCollector,
    EvaluationSummaryCollector,
    ExampleContextCollector,
    FailoverCollector,
)
from .models import TelemetryEvent, TelemetryPayload
from .transport_queue import (
    EXAMPLE_CONTEXT_SEEN_CAP,
    SHUTDOWN_FLUSH_DEADLINE_MS,
    TELEMETRY_DEFAULTS,
    TelemetryConfig,
    TelemetryHttpResult,
    TelemetryRequestError,
    TelemetryTransportQueue,
)

_LOG = logging.getLogger(__name__)


def _positive_or(value: Optional[float], fallback: int) -> int:
    """A positive finite number, else the default (same handling as sdk-node)."""
    try:
        if value is not None and float(value) > 0 and float(value) != float("inf"):
            return int(value)
    except (TypeError, ValueError):
        pass
    return fallback


class TelemetryReporter:
    """Drains the collectors once per tick and hands the serialized window to a
    :class:`TelemetryTransportQueue`, which retains failed batches byte-for-byte
    and resends them under the transport policy (qfg-y8je.7: 60s ticks, one
    POST in flight, 30s floor after a failure, Retry-After, 5 batches / 2MB /
    5 min retention, disable on 401/403/404). Mirrors sdk-node's reporter.

    The tick runs on a daemon thread. ``close()`` (and interpreter exit, via
    ``atexit``) gives the live window one POST with a 5s deadline and never
    drains the retained queue.
    """

    def __init__(
        self,
        telemetry_url: str,
        sdk_key: str,
        instance_hash: str = "",
        collect_evaluation_summaries: bool = True,
        context_upload_mode: str = "periodic_example",
        interval: Optional[float] = None,
        *,
        flush_interval_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
        connect_timeout_ms: Optional[int] = None,
        max_retained_batches: Optional[int] = None,
        max_retained_bytes: Optional[int] = None,
        max_retained_age_ms: Optional[int] = None,
        max_evaluation_summaries: Optional[int] = None,
        max_context_shape_fields: Optional[int] = None,
        max_example_contexts: Optional[int] = None,
        clock: Optional[TelemetryClock] = None,
    ) -> None:
        """``interval`` (seconds) is the pre-1.5 name of the flush interval and
        is used when ``flush_interval_ms`` is not set."""
        self.telemetry_url = telemetry_url.rstrip("/")
        self.sdk_key = sdk_key
        self.instance_hash = instance_hash
        self._clock: TelemetryClock = clock or REAL_CLOCK

        if flush_interval_ms is None and interval is not None:
            flush_interval_ms = int(float(interval) * 1000)
        d = TELEMETRY_DEFAULTS
        self.config = TelemetryConfig(
            flush_interval_ms=_positive_or(flush_interval_ms, d.flush_interval_ms),
            timeout_ms=_positive_or(timeout_ms, d.timeout_ms),
            connect_timeout_ms=_positive_or(connect_timeout_ms, d.connect_timeout_ms),
            max_retained_batches=_positive_or(max_retained_batches, d.max_retained_batches),
            max_retained_bytes=_positive_or(max_retained_bytes, d.max_retained_bytes),
            max_retained_age_ms=_positive_or(max_retained_age_ms, d.max_retained_age_ms),
            max_evaluation_summaries=_positive_or(
                max_evaluation_summaries, d.max_evaluation_summaries
            ),
            max_context_shape_fields=_positive_or(
                max_context_shape_fields, d.max_context_shape_fields
            ),
            max_example_contexts=_positive_or(max_example_contexts, d.max_example_contexts),
        )

        self._eval_collector: Optional[EvaluationSummaryCollector] = EvaluationSummaryCollector(
            enabled=collect_evaluation_summaries,
            max_data_size=self.config.max_evaluation_summaries,
        )
        self._ctx_collector = ContextShapeCollector(
            context_upload_mode=context_upload_mode,
            max_data_size=self.config.max_context_shape_fields,
        )
        self._example_collector = ExampleContextCollector(
            context_upload_mode=context_upload_mode,
            max_data_size=self.config.max_example_contexts,
            max_seen=EXAMPLE_CONTEXT_SEEN_CAP,
        )
        # Failover counters (qfg-41nh.18) carry no user data and are the
        # operational signal for the secondary-delivery hardening, so they ride
        # any enabled telemetry stream regardless of the eval/context opt-outs.
        # The reporter itself is only constructed when telemetry is enabled, so
        # this still honors a full telemetry opt-out.
        self._failover_collector = FailoverCollector()

        credentials = base64.b64encode(f"1:{self.sdk_key}".encode()).decode()
        self._headers: Dict[str, str] = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "X-Quonfig-SDK-Version": f"python-{QUONFIG_VERSION}",
            # No keep-alive: a 60s tick against Fly's 60s idle close would race
            # a pooled socket into a reset, and no idle socket outlives close().
            "Connection": "close",
        }
        self._session = requests.Session()
        self._queue = TelemetryTransportQueue(
            send=self._send,
            telemetry_url=f"{self.telemetry_url}/api/v1/telemetry/",
            config=self.config,
            on_disabled=self._on_disabled,
            clock=self._clock,
        )

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # One tick (serialize + drain) at a time: P2's one POST in flight. The
        # timer thread skips a tick when this is held; the public flush() waits.
        self._tick_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        # The at-exit final flush only runs in the process that built this
        # reporter: a forked child inherits the object (and the atexit hook)
        # but the window is the parent's to send (qfg-lv4n, the sdk-ruby
        # at_exit re-post bug).
        self._owner_pid = os.getpid()

    @property
    def interval(self) -> float:
        """Flush interval in seconds (pre-1.5 attribute, kept for callers)."""
        return self.config.flush_interval_ms / 1000.0

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._closed or self._queue.disabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="quonfig-telemetry")
        self._thread.start()
        _register_for_exit_flush(self)

    def _loop(self) -> None:
        """Fixed cadence: tick k fires at start + k * interval, however long a
        drain takes; ticks that fall due while one is still running are skipped."""
        interval = float(self.config.flush_interval_ms)
        next_at = self._clock.now_ms() + interval
        while not self._stop_event.is_set():
            wait_ms = next_at - self._clock.now_ms()
            if wait_ms > 0:
                if self._clock.wait(self._stop_event, wait_ms / 1000.0):
                    return
                continue
            while next_at <= self._clock.now_ms():
                next_at += interval
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - telemetry never kills its own loop
                _LOG.debug("[quonfig] Telemetry tick failed: %s: %s", type(e).__name__, e)

    def tick(self) -> None:
        """One tick of the contract's model: skip if closed, disabled or a POST
        is in flight (P2; the live window keeps aggregating); expire aged
        batches; skip if the 30s floor or Retry-After has not elapsed; serialize
        the live window once and append it; drain oldest-first."""
        if self._closed or self._queue.disabled:
            return
        if not self._tick_lock.acquire(blocking=False):
            return
        try:
            self._run_tick()
        finally:
            self._tick_lock.release()

    def _run_tick(self) -> None:
        if self._closed or self._queue.disabled:
            return
        self._queue.expire()
        if not self._queue.send_allowed():
            return
        body = self._serialize_window()
        if body is not None:
            self._queue.append(body)
        self._queue.drain()

    def flush(self) -> None:
        """Send the live window now — the public, thread-safe flush (qfg-0xj3.3).

        Intended for serverless handlers (AWS Lambda, Vercel): the periodic
        daemon timer does not fire while the execution environment is frozen,
        so call this before returning a response. Waits for an in-flight POST
        first (bounded by the request timeout), then runs a tick, so after a
        failure it respects the 30s floor and Retry-After. Never raises.
        """
        if self._closed or self._queue.disabled:
            return
        try:
            wait_s = (self.config.timeout_ms + self.config.connect_timeout_ms) / 1000.0
            if not self._tick_lock.acquire(timeout=wait_s):
                return
            try:
                self._run_tick()
            finally:
                self._tick_lock.release()
        except Exception as e:  # noqa: BLE001 - telemetry never breaks the caller
            _LOG.debug("[quonfig] Telemetry flush failed: %s: %s", type(e).__name__, e)

    def close(self) -> None:
        """Shutdown (P8): stop the timer, then give the live window one POST
        with a 5s deadline. The retained queue is not drained. Idempotent and
        bounded: returns within the deadline even against a hanging endpoint."""
        worker = self._begin_close()
        if worker is not None:
            worker.join(min(SHUTDOWN_FLUSH_DEADLINE_MS, self.config.timeout_ms) / 1000.0)
            worker.abandon_if_running()

    def stop(self) -> None:
        """Deprecated alias for :meth:`close` (kept for callers of the class)."""
        self.close()

    def _begin_close(self) -> Optional["_FinalFlush"]:
        with self._close_lock:
            if self._closed:
                return None
            self._closed = True
        self._stop_event.set()
        self._queue.stop()
        if self._queue.disabled:
            return None
        body = self._serialize_window()
        if body is None:
            return None
        worker = _FinalFlush(self, body)
        worker.start()
        return worker

    # -- recording ------------------------------------------------------------

    def record_evaluation(self, result: EvalResult) -> None:
        if self._eval_collector is not None:
            self._eval_collector.record(result)

    def record_context(self, contexts: Contexts) -> None:
        self._ctx_collector.record(contexts)
        self._example_collector.record(contexts)

    def record_hedge_fired(self) -> None:
        """Record one config-fetch cycle whose hedge fired the secondary leg."""
        self._failover_collector.record_hedge_fired()

    def record_guard_rejected(self) -> None:
        """Record one STRICTLY OLDER payload dropped by the reject-older
        ordering guard. Equal-generation re-delivery is dropped too but is not
        a backwards move, so it is not recorded here (qfg-rr5b)."""
        self._failover_collector.record_guard_rejected()

    def record_resolved_from(self, source_index: int) -> None:
        """Record one successful HTTP install by the leg that served it
        (source_index 0 = primary, > 0 = secondary; a negative index is
        ignored)."""
        self._failover_collector.record_resolved_from(source_index)

    # -- test-visible state ---------------------------------------------------

    def debug_state(self) -> Dict[str, Any]:
        """The contract's retained_count / retained_bytes / telemetry_enabled."""
        return {
            "retained_count": self._queue.retained_count,
            "retained_bytes": self._queue.retained_bytes,
            "enabled": not self._queue.disabled,
            "in_flight": self._queue.busy,
            "closed": self._closed,
        }

    # -- internals ------------------------------------------------------------

    def _on_disabled(self) -> None:
        self._stop_event.set()
        if self._eval_collector is not None:
            self._eval_collector.disable()
        self._ctx_collector.disable()
        self._example_collector.disable()
        self._failover_collector.disable()

    def _serialize_window(self) -> Optional[bytes]:
        """Drain the collectors into one serialized payload. This is the only
        serialization: the queue stores and resends these exact bytes (P5, P9)."""
        events: List[TelemetryEvent] = []
        if self._eval_collector is not None:
            ev = self._eval_collector.drain()
            if ev:
                events.append(ev)
        for collector in (self._ctx_collector, self._example_collector, self._failover_collector):
            ev = collector.drain()
            if ev:
                events.append(ev)
        if not events:
            return None
        payload = TelemetryPayload(instance_hash=self.instance_hash, events=events)
        try:
            # Same encoder settings requests' ``json=`` used before 1.5.
            return json.dumps(payload.to_dict(), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            _LOG.warning(
                "[quonfig] Telemetry window could not be serialized and was dropped: %s", e
            )
            return None

    def _send(self, body: bytes, timeout_ms: int, connect_timeout_ms: int) -> TelemetryHttpResult:
        """One POST. ``requests`` takes (connect, read) timeouts: connect/TLS is
        bounded by ``connect_timeout_ms`` and every socket read by
        ``timeout_ms``, so a server that never answers is abandoned at
        ``timeout_ms``."""
        url = f"{self.telemetry_url}/api/v1/telemetry/"
        try:
            resp = self._session.post(
                url,
                data=body,
                headers=self._headers,
                timeout=(connect_timeout_ms / 1000.0, timeout_ms / 1000.0),
                stream=True,
            )
        except requests.exceptions.ConnectTimeout as e:
            raise TelemetryRequestError("connect timeout", str(e)) from e
        except requests.exceptions.Timeout as e:
            raise TelemetryRequestError("timeout", str(e)) from e
        except requests.exceptions.RequestException as e:
            raise TelemetryRequestError("network", type(e).__name__) from e
        try:
            try:
                snippet = resp.raw.read(1024, decode_content=True) or b""
            except Exception:  # noqa: BLE001 - the status is what matters
                snippet = b""
            return TelemetryHttpResult(
                status=resp.status_code,
                retry_after=resp.headers.get("Retry-After"),
                body_snippet=snippet.decode("utf-8", errors="replace"),
            )
        finally:
            resp.close()


class _FinalFlush:
    """The close()/exit POST of the live window, on a daemon thread so the
    caller (or the interpreter) waits at most the deadline for it."""

    def __init__(self, reporter: TelemetryReporter, body: bytes) -> None:
        self._reporter = reporter
        self._body = body
        self._done = threading.Event()
        self._abandoned = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        try:
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="quonfig-telemetry-final"
            )
            self._thread.start()
        except RuntimeError:
            # No new threads (late interpreter shutdown): send inline; the
            # request timeouts still bound it at the deadline.
            self._thread = None
            self._run()

    def _run(self) -> None:
        r = self._reporter
        deadline = min(SHUTDOWN_FLUSH_DEADLINE_MS, r.config.timeout_ms)
        try:
            result = r._queue.send_final(self._body, deadline)
        finally:
            self._done.set()
        if result is not None and not self._abandoned:
            r._queue.log_final_failure(result, len(self._body))

    def join(self, timeout_s: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout_s)

    def remaining_join(self, deadline_monotonic: float) -> None:
        if self._thread is not None:
            self._thread.join(max(0.0, deadline_monotonic - time.monotonic()))

    def abandon_if_running(self) -> None:
        if not self._done.is_set():
            self._abandoned = True
            self._reporter._queue.log_final_failure("timeout", len(self._body))


# -- interpreter exit (P8) ----------------------------------------------------

_exit_reporters: "weakref.WeakSet[TelemetryReporter]" = weakref.WeakSet()


def _register_for_exit_flush(reporter: TelemetryReporter) -> None:
    _exit_reporters.add(reporter)


def _flush_all_at_exit() -> None:
    """Give every live reporter's window one POST, all in parallel, within one
    shared 5s deadline. Never raises and never waits longer than that."""
    try:
        pid = os.getpid()
        reporters = [r for r in list(_exit_reporters) if r._owner_pid == pid]
        workers = []
        for r in reporters:
            try:
                w = r._begin_close()
            except Exception:  # noqa: BLE001 - exit must not fail on telemetry
                continue
            if w is not None:
                workers.append(w)
        deadline = time.monotonic() + SHUTDOWN_FLUSH_DEADLINE_MS / 1000.0
        for w in workers:
            w.remaining_join(deadline)
            w.abandon_if_running()
    except Exception:  # noqa: BLE001
        pass


atexit.register(_flush_all_at_exit)
