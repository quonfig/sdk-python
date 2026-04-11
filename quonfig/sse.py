from __future__ import annotations

import json
import random
import threading
from typing import Optional

import requests
import sseclient  # type: ignore

from .store import ConfigStore
from .transport import Transport
from .types import ConfigEnvelope


class SSEClient:
    def __init__(
        self,
        transport: Transport,
        store: ConfigStore,
        shutdown_event: threading.Event,
    ) -> None:
        self.transport = transport
        self.store = store
        self.shutdown_event = shutdown_event
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="quonfig-sse"
        )
        self._thread.start()

    def _loop(self) -> None:
        backoff = 1.0
        while not self.shutdown_event.is_set():
            try:
                url = f"{self.transport._current_url()}/api/v2/sse/config"
                headers = self.transport._headers({"Accept": "text/event-stream"})
                response = requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=(5, 60),
                )
                response.raise_for_status()
                client = sseclient.SSEClient(response)
                backoff = 1.0  # Reset on successful connection
                for event in client.events():
                    if self.shutdown_event.is_set():
                        break
                    if event.data:
                        try:
                            envelope = ConfigEnvelope.from_dict(json.loads(event.data))
                            self.store.update(envelope)
                        except Exception:
                            pass
            except Exception:
                if self.shutdown_event.is_set():
                    break
                # Exponential backoff with jitter
                self.shutdown_event.wait(backoff)
                backoff = min(backoff * 2 * (0.8 + 0.4 * random.random()), 60.0)
