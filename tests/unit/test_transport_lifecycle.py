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

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from quonfig import Quonfig
from quonfig.exceptions import QuonfigInitTimeoutError
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


def test_close_racing_an_in_flight_initial_fetch_still_raises() -> None:
    """``close()`` on a NEVER-FORKED client must not latch init (qfg-b8kw).

    1.4.0 added ``if not self._initialized.is_set(): self._finish_init()`` to
    ``close()`` so a forked child closed before its first use answers defaults
    instantly (covered by ``test_forking.py::test_p3_...``). That branch ran on
    EVERY close, so on a live client with ``on_init_failure="raise"`` whose
    initial fetch was still in flight, a getter blocked in ``_wait_initialized``
    on another thread unblocked and returned its default instead of blocking to
    ``init_timeout_ms`` and raising ``QuonfigInitTimeoutError``. The latch is
    now scoped to the forked-child path, restoring the pre-1.4.0 semantics
    here.
    """
    server = _SlowServer()
    client = Quonfig(
        sdk_key="sdk-test",
        api_urls=[server.url],
        enable_sse=False,
        fallback_poll_enabled=False,
        init_timeout_ms=1000,
        on_init_failure="raise",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    outcome: list[str] = []
    started = threading.Event()

    def getter() -> None:
        started.set()
        try:
            outcome.append(f"value={client.get_string('k', default='D')}")
        except QuonfigInitTimeoutError:
            outcome.append("raised=QuonfigInitTimeoutError")
        except BaseException as exc:  # noqa: BLE001 — reported, not raised
            outcome.append(f"other={type(exc).__name__}")

    try:
        client.init()
        thread = threading.Thread(target=getter, daemon=True)
        thread.start()
        assert started.wait(timeout=5)
        # Land close() well inside the 1000ms init window, while the getter is
        # parked in _wait_initialized and the fetch is still in flight.
        time.sleep(0.1)
        client.close()
        thread.join(timeout=10)
        assert not thread.is_alive(), "the getter never returned"
        assert outcome == ["raised=QuonfigInitTimeoutError"], (
            f"close() latched init on a never-forked client: {outcome}"
        )
    finally:
        client.close()
        server.close()
