"""Regression test for qfg-q8lb: SSE reconnect backoff cap.

The error-path backoff grew to a 60 s cap, so after a long outage the next
reconnect attempt could land up to 60 s after the server came back, past the
chaos 05/06 45 s recovery window. sdk-go (``sse_client.go`` MaxDelay) and
sdk-node (``SSE_RECONNECT_MAX_DELAY_MS``) cap at 30 s; this SDK now does too.
"""

from __future__ import annotations

import threading
from typing import List
from unittest import mock

import requests  # type: ignore[import-untyped]

from quonfig.sse import SSEClient
from quonfig.store import ConfigStore
from quonfig.transport import Transport

_ATTEMPTS = 40


def _recorded_backoffs() -> List[float]:
    transport = Transport(["http://127.0.0.1:1"], "sk")
    shutdown = threading.Event()
    sleeps: List[float] = []

    def wait_hook(_kind: str, seconds: float) -> float:
        sleeps.append(seconds)
        if len(sleeps) >= _ATTEMPTS:
            shutdown.set()
        return 0.0

    def fail(*_args: object, **_kwargs: object) -> None:
        raise requests.ConnectionError("down")

    sse = SSEClient(transport, ConfigStore(), shutdown, wait_hook=wait_hook)
    with mock.patch("quonfig.sse.requests.get", side_effect=fail):
        sse._loop()
    return sleeps


def test_reconnect_backoff_caps_at_30s() -> None:
    sleeps = _recorded_backoffs()
    assert len(sleeps) == _ATTEMPTS
    assert max(sleeps) <= 30.0, f"backoff exceeded 30 s cap: max={max(sleeps):.1f}"
    # The cap is reached (the sequence grows to it, not stalls below it).
    assert sleeps[-1] == 30.0, sleeps[-5:]


def test_reconnect_backoff_still_starts_at_1s_and_grows() -> None:
    sleeps = _recorded_backoffs()
    assert sleeps[0] == 1.0
    assert sleeps[3] > sleeps[0]
