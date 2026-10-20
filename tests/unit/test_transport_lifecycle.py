"""Regression tests for Transport / Quonfig lifecycle wiring.

Two bugs caught in code review:

1. ``Quonfig.close()`` did not close the underlying ``requests.Session``,
   leaking sockets/FDs in long-running backends that recycle clients.

2. ``init_timeout_ms`` capped the per-request HTTP timeout. With a sub-second
   init timeout this surfaced as a generic ``requests.Timeout`` from the
   background fetch (silently swallowed) instead of letting ``init()`` raise
   ``QuonfigInitTimeoutError``.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    # the init-timeout is enforced by `init()`, not by the per-request
    # timeout, so a tiny init_timeout_ms does NOT cap the request timeout.
    assert client._transport.timeout == 3.0


def test_transport_close_releases_session() -> None:
    t = Transport(api_urls=["http://localhost:0"], sdk_key="sdk-test")
    with patch.object(t._session, "close") as session_close:
        t.close()
        session_close.assert_called_once()


class _SlowServer:
    """A config endpoint that never answers within the test's init timeout, so
    the initial fetch is genuinely still in flight when ``close()`` lands."""

    def __init__(self) -> None:
        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                time.sleep(30)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        _host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()


def test_close_during_a_blocking_init_returns_init_promptly_without_latching_ready() -> None:
    """``close()`` on a NEVER-FORKED client whose init is still in flight
    (qfg-b8kw, restated for 2.0.0 / qfg-9p4i.8).

    ``init()`` now blocks until a config loads or ``init_timeout_ms`` passes.
    A ``close()`` from another thread during that wait must end the wait at
    once: ``init()`` returns (the caller closed the client, so there is no
    failure to report) instead of hanging on the slow fetch, and the client is
    NOT reported ready, because nothing was installed.
    """
    server = _SlowServer()
    client = Quonfig(
        sdk_key="sdk-test",
        api_urls=[server.url],
        enable_sse=False,
        fallback_poll_enabled=False,
        init_timeout_ms=10_000,
        on_init_failure="fail",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    outcome: list[str] = []

    def run_init() -> None:
        try:
            client.init()
            outcome.append("returned")
        except BaseException as exc:  # noqa: BLE001 — reported, not raised
            outcome.append(f"raised={type(exc).__name__}")

    try:
        thread = threading.Thread(target=run_init, daemon=True)
        thread.start()
        # Land close() well inside the 10s init window, while the fetch is
        # still in flight and init() is blocked on it.
        time.sleep(0.3)
        assert thread.is_alive(), "init() returned before the fetch settled"
        start = time.monotonic()
        client.close()
        thread.join(timeout=5)
        assert not thread.is_alive(), "init() kept blocking after close()"
        assert time.monotonic() - start < 2.0
        assert outcome == ["returned"], outcome
        assert client.is_ready() is False
        assert client.get_string("k", default="D") == "D"
    finally:
        client.close()
        server.close()
