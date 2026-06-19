"""Regression tests for Transport / Quonfig lifecycle wiring.

Two bugs caught in code review:

1. ``Quonfig.close()`` did not close the underlying ``requests.Session``,
   leaking sockets/FDs in long-running backends that recycle clients.

2. ``init_timeout_ms`` capped the per-request HTTP timeout. With a sub-second
   init timeout this surfaced as a generic ``requests.Timeout`` from the
   background fetch (silently swallowed) instead of letting
   ``_wait_initialized`` raise ``QuonfigInitTimeoutError``.
"""

from __future__ import annotations

from unittest.mock import patch

from quonfig import Quonfig
from quonfig.transport import Transport


def test_close_releases_transport_session() -> None:
    client = Quonfig(sdk_key="sdk-test", api_urls=["http://localhost:0"])
    assert client._transport is not None
    with patch.object(client._transport._session, "close") as session_close:
        client.close()
        session_close.assert_called_once()


def test_close_is_safe_when_no_transport() -> None:
    # datadir-only mode never creates a transport; close() must not blow up.
    client = Quonfig(datadir="/nonexistent", environment="production")
    assert client._transport is None
    client.close()  # should be a no-op


def test_init_timeout_does_not_cap_request_timeout() -> None:
    client = Quonfig(
        sdk_key="sdk-test",
        api_urls=["http://localhost:0"],
        init_timeout_ms=10,
    )
    assert client._transport is not None
    # Request timeout stays at the per-URL config-fetch default (~3s, qfg-7h5d.1.8);
    # the init-timeout is enforced by `_wait_initialized`, not by the per-request
    # timeout, so a tiny init_timeout_ms does NOT cap the request timeout.
    assert client._transport.timeout == 3.0


def test_transport_close_releases_session() -> None:
    t = Transport(api_urls=["http://localhost:0"], sdk_key="sdk-test")
    with patch.object(t._session, "close") as session_close:
        t.close()
        session_close.assert_called_once()
