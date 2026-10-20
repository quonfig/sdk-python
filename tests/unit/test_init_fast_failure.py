"""qfg-9p4i.1: a fast boot failure must honor ``on_init_failure="raise"``.

When both legs fail fast at boot (connection refused, DNS failure, 401, 5xx),
the initial fetch used to latch init as done with an empty store, so the
default ``on_init_failure="raise"`` silently served code defaults: getters with
a default returned it at once and nothing raised. The documented default (and
the init-failure design, project/plans/2026-10-05-init-failure-design.md,
section E step 1) promises a raise.

These tests pin the 1.x behavior: the raise stays lazy (at the getter, the same
place the timeout raise happens), but it no longer waits for the timeout when
the initial fetch has already failed on every leg. The client object survives
and keeps retrying in the background; once a config installs, getters stop
raising (no permanently dead clients).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import pytest

from quonfig import Quonfig
from quonfig.exceptions import QuonfigInitTimeoutError


def _envelope_json() -> bytes:
    return json.dumps(
        {
            "configs": [],
            "meta": {"version": "gen-1", "environment": "Production", "generation": 1},
        }
    ).encode()


class _Upstream:
    """Answers every GET with ``status`` (200 serves an empty envelope).
    ``status`` can be switched at runtime to model an outage healing."""

    def __init__(self, status: int) -> None:
        self.status = status
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if outer.status == 200:
                    body = _envelope_json()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                else:
                    body = b"nope"
                    self.send_response(outer.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        _, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()


def _client(urls: list, on_init_failure: Optional[str] = None, **kw: object) -> Quonfig:
    opts: dict = dict(
        sdk_key="test-backend-key",
        api_urls=urls,
        enable_sse=False,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        init_timeout_ms=10_000,
    )
    if on_init_failure is not None:
        opts["on_init_failure"] = on_init_failure
    opts.update(kw)
    return Quonfig(**opts)


def _assert_raises_fast(client: Quonfig) -> None:
    start = time.monotonic()
    with pytest.raises(QuonfigInitTimeoutError):
        client.get_string("some.key", default="DEFAULT")
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"raised after {elapsed:.2f}s; a fast failure must not wait out 10s"


def test_401_on_both_legs_raises_under_default_on_init_failure() -> None:
    a, b = _Upstream(401), _Upstream(401)
    client = _client([a.url, b.url], fallback_poll_enabled=False)
    try:
        client.init()
        _assert_raises_fast(client)
    finally:
        client.close()
        a.close()
        b.close()


def test_connection_refused_on_both_legs_raises_under_explicit_raise() -> None:
    client = _client(
        ["http://127.0.0.1:1", "http://127.0.0.1:1"],
        on_init_failure="raise",
        fallback_poll_enabled=False,
    )
    try:
        client.init()
        _assert_raises_fast(client)
        # Every getter keeps raising while nothing is installed, not just the first.
        _assert_raises_fast(client)
    finally:
        client.close()


def test_return_zero_value_still_serves_defaults_on_fast_failure() -> None:
    client = _client(
        ["http://127.0.0.1:1", "http://127.0.0.1:1"],
        on_init_failure="return_zero_value",
        fallback_poll_enabled=False,
    )
    try:
        client.init()
        assert client.get_string("some.key", default="DEFAULT") == "DEFAULT"
    finally:
        client.close()


def test_client_that_survives_a_caught_raise_keeps_retrying_and_heals() -> None:
    a, b = _Upstream(503), _Upstream(503)
    client = _client([a.url, b.url], fallback_poll_interval_ms=200)
    try:
        client.init()
        _assert_raises_fast(client)

        a.status = 200
        b.status = 200
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not client.ready():
            time.sleep(0.05)
        assert client.ready(), "client never healed after delivery came back"
        assert client.get_string("some.key", default="DEFAULT") == "DEFAULT"
    finally:
        client.close()
        a.close()
        b.close()
