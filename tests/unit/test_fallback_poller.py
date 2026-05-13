"""Layer 2 fallback poller — engages only when SSE is unavailable (qfg-47c2.8).

Mirrors sdk-node's behaviour: the fallback HTTP poller is OFF by default while
SSE is connected, engages on initial-SSE-failure or after a grace period
following a disconnect, and disengages immediately when SSE recovers.

This replaces the previous always-on parallel poll in
``Transport.start_polling`` (which doubled bandwidth and had no reconcile
logic).
"""

from __future__ import annotations

import threading
import time

from quonfig import Quonfig


def _make_client(**overrides: object) -> Quonfig:
    kwargs: dict = dict(
        sdk_key="sdk-test",
        api_urls=["http://localhost:0"],
        fallback_poll_enabled=True,
        fallback_poll_interval_ms=60000,
    )
    kwargs.update(overrides)
    return Quonfig(**kwargs)  # type: ignore[arg-type]


def test_fallback_inactive_by_default() -> None:
    client = _make_client()
    try:
        assert client.fallback_poller_active() is False
    finally:
        client.close()


def test_fallback_engages_on_initial_sse_failure() -> None:
    """Before any successful 'connected', an 'error' edge engages the poller now."""
    client = _make_client()
    try:
        assert client.fallback_poller_active() is False
        client._handle_sse_state_change("error")
        assert client.fallback_poller_active() is True
    finally:
        client.close()


def test_fallback_disengages_on_sse_recovered() -> None:
    client = _make_client()
    try:
        client._handle_sse_state_change("error")
        assert client.fallback_poller_active() is True
        client._handle_sse_state_change("connected")
        assert client.fallback_poller_active() is False
    finally:
        client.close()


def test_disconnect_after_connect_does_not_engage_immediately() -> None:
    """A connected→error edge must wait a grace period (2x interval) before
    engaging. This is what stops the SSE library's own auto-reconnect from
    racing the fallback poller."""
    client = _make_client(fallback_poll_interval_ms=60000)
    try:
        client._handle_sse_state_change("connected")
        client._handle_sse_state_change("error")
        # Within grace window: not yet engaged, but a timer is pending.
        assert client.fallback_poller_active() is False
        assert client._fallback_engage_timer is not None
    finally:
        client.close()


def test_grace_engage_fires_after_interval() -> None:
    """When the grace timer elapses without reconnect, the poller engages."""
    # Tiny interval so the grace period (2x interval) elapses fast in test.
    client = _make_client(fallback_poll_interval_ms=50)
    try:
        client._handle_sse_state_change("connected")
        client._handle_sse_state_change("error")
        # 2x 50ms = 100ms grace; allow generous slack for CI.
        deadline = time.time() + 2.0
        while time.time() < deadline and not client.fallback_poller_active():
            time.sleep(0.01)
        assert client.fallback_poller_active() is True
    finally:
        client.close()


def test_reconnect_during_grace_cancels_engage() -> None:
    """If SSE reconnects before the grace timer fires, the poller never engages."""
    client = _make_client(fallback_poll_interval_ms=60000)
    try:
        client._handle_sse_state_change("connected")
        client._handle_sse_state_change("error")
        assert client._fallback_engage_timer is not None
        client._handle_sse_state_change("connected")
        assert client._fallback_engage_timer is None
        assert client.fallback_poller_active() is False
    finally:
        client.close()


def test_fallback_disabled_never_engages() -> None:
    client = _make_client(fallback_poll_enabled=False)
    try:
        client._handle_sse_state_change("error")
        assert client.fallback_poller_active() is False
    finally:
        client.close()


def test_engaged_poller_calls_transport_fetch(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Once engaged, the poller actually calls Transport.fetch on its interval."""
    client = _make_client(fallback_poll_interval_ms=50)
    try:
        called = threading.Event()

        def fake_fetch(etag=None):  # type: ignore[no-untyped-def]
            called.set()
            return None

        assert client._transport is not None
        monkeypatch.setattr(client._transport, "fetch", fake_fetch)

        client._handle_sse_state_change("error")
        assert client.fallback_poller_active() is True
        assert called.wait(2.0), "transport.fetch was not called by fallback poller"
    finally:
        client.close()
