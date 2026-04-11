from __future__ import annotations

import base64
import threading
import time
from typing import List, Optional

import requests

from ..types import Contexts, EvalResult
from .collectors import ContextShapeCollector, EvaluationSummaryCollector
from .models import TelemetryPayload

QUONFIG_VERSION = "0.0.1"


class TelemetryReporter:
    """
    Daemon thread that periodically flushes evaluation summaries and context shapes
    to the Quonfig telemetry endpoint.
    """

    def __init__(
        self,
        telemetry_url: str,
        sdk_key: str,
        collect_evaluation_summaries: bool = True,
        collect_context_shapes: bool = True,
        interval: float = 30.0,
    ) -> None:
        self.telemetry_url = telemetry_url.rstrip("/")
        self.sdk_key = sdk_key
        self.collect_evaluation_summaries = collect_evaluation_summaries
        self.collect_context_shapes = collect_context_shapes
        self.interval = interval

        self._eval_collector = EvaluationSummaryCollector() if collect_evaluation_summaries else None
        self._ctx_collector = ContextShapeCollector() if collect_context_shapes else None

        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._session = requests.Session()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="quonfig-telemetry"
        )
        self._thread.start()

    def record_evaluation(self, result: EvalResult) -> None:
        if self._eval_collector is not None:
            self._eval_collector.record(result)

    def record_context(self, contexts: Contexts) -> None:
        if self._ctx_collector is not None:
            self._ctx_collector.record(contexts)

    def stop(self) -> None:
        """Signal shutdown and do a final flush."""
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
        summaries, ctx_shapes = [], []
        start_millis, end_millis = 0, 0

        if self._eval_collector is not None:
            summaries, start_millis, end_millis = self._eval_collector.flush()

        if self._ctx_collector is not None:
            ctx_shapes = self._ctx_collector.flush()

        if not summaries and not ctx_shapes:
            return

        payload = TelemetryPayload(
            evaluation_summaries=summaries,
            context_shapes=ctx_shapes,
            start_millis=start_millis,
            end_millis=end_millis,
        )

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
