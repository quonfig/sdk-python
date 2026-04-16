from __future__ import annotations

import base64
import threading
import time
from typing import List, Optional

import requests  # type: ignore[import-untyped]

from ..types import Contexts, EvalResult
from .collectors import ContextShapeCollector, EvaluationSummaryCollector, ExampleContextCollector
from .models import TelemetryEvent, TelemetryPayload

QUONFIG_VERSION = "0.0.1"


class TelemetryReporter:
    """Daemon thread that periodically flushes telemetry to the Quonfig endpoint."""

    def __init__(
        self,
        telemetry_url: str,
        sdk_key: str,
        instance_hash: str = "",
        collect_evaluation_summaries: bool = True,
        context_upload_mode: str = "shapes_only",
        interval: float = 30.0,
    ) -> None:
        self.telemetry_url = telemetry_url.rstrip("/")
        self.sdk_key = sdk_key
        self.instance_hash = instance_hash
        self.interval = interval

        self._eval_collector: Optional[EvaluationSummaryCollector] = (
            EvaluationSummaryCollector(enabled=collect_evaluation_summaries)
        )
        self._ctx_collector = ContextShapeCollector(context_upload_mode=context_upload_mode)
        self._example_collector = ExampleContextCollector(context_upload_mode=context_upload_mode)

        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._session = requests.Session()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="quonfig-telemetry")
        self._thread.start()

    def record_evaluation(self, result: EvalResult) -> None:
        if self._eval_collector is not None:
            self._eval_collector.record(result)

    def record_context(self, contexts: Contexts) -> None:
        self._ctx_collector.record(contexts)
        self._example_collector.record(contexts)

    def stop(self) -> None:
        self._shutdown.set()
        try:
            self._flush()
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._shutdown.is_set():
            self._shutdown.wait(self.interval)
            if self._shutdown.is_set():
                break
            try:
                self._flush()
            except Exception:
                pass

    def _flush(self) -> None:
        events: List[TelemetryEvent] = []

        if self._eval_collector is not None:
            ev = self._eval_collector.drain()
            if ev:
                events.append(ev)

        ev = self._ctx_collector.drain()
        if ev:
            events.append(ev)

        ev = self._example_collector.drain()
        if ev:
            events.append(ev)

        if not events:
            return

        payload = TelemetryPayload(instance_hash=self.instance_hash, events=events)

        credentials = base64.b64encode(f"1:{self.sdk_key}".encode()).decode()
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "X-Quonfig-SDK-Version": f"python/{QUONFIG_VERSION}",
        }

        backoff = 1.0
        for attempt in range(3):
            try:
                url = f"{self.telemetry_url}/api/v1/telemetry/"
                resp = self._session.post(
                    url, json=payload.to_dict(), headers=headers, timeout=10.0
                )
                resp.raise_for_status()
                return
            except Exception:
                if attempt < 2:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
