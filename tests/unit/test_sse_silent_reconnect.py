"""Regression test for qfg-47c2.31 — sdk-python SSE silent reconnects.

In chaos scenario 01 baseline (no toxics), sdk-python's chaos probe recorded
2 ``connected -> connecting`` edges over 30 wall-clock seconds. The cause is
``sseclient-py.SSEClient.events()`` returning cleanly mid-stream (clean EOF
from the underlying ``requests.Response`` iterator) — our ``_loop`` then fell
through to ``_emit('connecting')`` on the next iteration, registering a
spurious Layer 1 restart even though the customer-visible config and
``connection_state()`` were never disturbed.

The fix: treat a clean iterator exit as a **transparent reconnect** — do not
emit ``connecting`` again, just re-attempt. If the new attempt succeeds the
state stays ``connected``. If it fails with a real exception, the existing
``error`` path runs as before.
"""

from __future__ import annotations

import threading
from typing import Any, Iterator, List
from unittest.mock import patch

from quonfig.sse import SSEClient
from quonfig.store import ConfigStore
from quonfig.transport import Transport


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, *_a: Any, **_k: Any) -> Iterator[bytes]:
        return iter(())


def test_clean_eof_mid_stream_does_not_flip_state_to_connecting() -> None:
    """sseclient.events() returning cleanly must NOT trip a connected->connecting
    edge. Only the *initial* connecting emission is allowed — every subsequent
    iteration that ends in a clean EOF is a transparent reconnect."""
    transport = Transport(api_urls=["http://localhost:6550"], sdk_key="sk")
    store = ConfigStore()
    shutdown = threading.Event()

    states: List[str] = []
    state_lock = threading.Lock()

    # Bound the loop: after the SDK has had 3 chances to call events() we set
    # shutdown so the test terminates regardless of whether the bug is present.
    events_calls = {"n": 0}
    events_done = threading.Event()

    def mock_events() -> Iterator[Any]:
        events_calls["n"] += 1
        if events_calls["n"] >= 3:
            shutdown.set()
            events_done.set()
        return iter(())

    def state_listener(s: str) -> None:
        with state_lock:
            states.append(s)

    sse_client = SSEClient(transport, store, shutdown, state_listener=state_listener)

    with (
        patch("quonfig.sse.requests.get", return_value=_FakeResponse()),
        patch("quonfig.sse.sseclient.SSEClient") as mock_sse_lib,
    ):
        mock_sse_lib.return_value.events.side_effect = mock_events
        t = threading.Thread(target=sse_client._loop, daemon=True)
        t.start()
        events_done.wait(timeout=3.0)
        # Loop may still be mid-emit when wait returns; give it a moment.
        t.join(timeout=2.0)

    # The bug emits 'connecting' on EVERY loop iteration (one per clean EOF).
    # Post-fix: only the first iteration emits 'connecting'; later clean EOFs
    # do NOT flip state.
    connecting_count = sum(1 for s in states if s == "connecting")
    assert connecting_count == 1, (
        "clean sseclient EOF must reconnect transparently — got "
        f"{connecting_count} 'connecting' emissions; full sequence: {states}"
    )
    # And we should still have at least one 'connected' emission so the
    # listener sees the SDK is healthy.
    assert "connected" in states, f"expected at least one 'connected' emission; got: {states}"


def test_exception_path_still_flips_state_to_error() -> None:
    """A genuine connection failure must still emit 'error' (and the next
    attempt's 'connecting') — the fix only suppresses the silent-reconnect
    edge from a clean iterator exit, NOT real errors."""
    transport = Transport(api_urls=["http://localhost:6550"], sdk_key="sk")
    store = ConfigStore()
    shutdown = threading.Event()

    states: List[str] = []
    state_lock = threading.Lock()

    attempt = {"n": 0}

    def fake_get(*_a: Any, **_k: Any) -> _FakeResponse:
        attempt["n"] += 1
        if attempt["n"] == 1:
            return _FakeResponse()
        # Second attempt: raise so we can observe the error path.
        raise ConnectionError("simulated network failure")

    def mock_events() -> Iterator[Any]:
        # First attempt: events() yields nothing (clean EOF). Loop iterates,
        # second attempt's fake_get raises.
        return iter(())

    def state_listener(s: str) -> None:
        with state_lock:
            states.append(s)
            # Once we've observed the error path, stop the loop.
            if s == "error":
                shutdown.set()

    sse_client = SSEClient(transport, store, shutdown, state_listener=state_listener)

    with (
        patch("quonfig.sse.requests.get", side_effect=fake_get),
        patch("quonfig.sse.sseclient.SSEClient") as mock_sse_lib,
    ):
        mock_sse_lib.return_value.events.side_effect = mock_events
        t = threading.Thread(target=sse_client._loop, daemon=True)
        t.start()
        t.join(timeout=3.0)

    # Sequence we expect post-fix:
    #   connecting (first attempt) -> connected -> [clean EOF, NO state edge]
    #   -> [second attempt fails] -> error -> disconnected (shutdown set)
    assert "error" in states, f"real connection failure must emit 'error'; got: {states}"
    # Still exactly one 'connecting' — the clean EOF didn't add a second one.
    connecting_count = sum(1 for s in states if s == "connecting")
    assert connecting_count == 1, f"only the first attempt should emit 'connecting'; got: {states}"
