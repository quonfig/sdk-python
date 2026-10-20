"""The logger_path ``should_log`` form must not block while HTTP init is in
flight (qfg-goi1.2.30).

The stdlib filter and structlog processor call it on every log record; it used
to wait up to ``init_timeout_ms`` for init. Before init it now returns
"no opinion" (allow) immediately. Matches sdk-java f8282de (getLogLevel returns
empty before init). Since 2.0.0 (qfg-9p4i.8) ``init()`` itself blocks, so these
tests run it on a background thread and log from another one while it is in
flight.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator, Tuple

import pytest

from quonfig import Quonfig, QuonfigLoggerFilter

_DELAY_S = 2.0
_ENVELOPE = {
    "configs": [
        {
            "id": "ll-1",
            "key": "log-level.app",
            "type": "log_level",
            "valueType": "log_level",
            "sendToClientSdk": False,
            "default": {
                "rules": [
                    {
                        "criteria": [{"operator": "ALWAYS_TRUE"}],
                        "value": {"type": "log_level", "value": "ERROR"},
                    }
                ]
            },
        },
        {
            "id": "s-1",
            "key": "plain",
            "type": "config",
            "valueType": "string",
            "default": {
                "rules": [
                    {
                        "criteria": [{"operator": "ALWAYS_TRUE"}],
                        "value": {"type": "string", "value": "ok"},
                    }
                ]
            },
        },
    ],
    "meta": {"version": "v1", "environment": "production", "generation": 1},
}


class _SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if "/api/v2/configs" in self.path:
            time.sleep(_DELAY_S)
        body = json.dumps(_ENVELOPE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def slow_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _client(url: str) -> Quonfig:
    client = Quonfig(
        sdk_key="qf_sk_development_0000_test",
        api_urls=[url],
        logger_key="log-level.app",
        enable_sse=False,
        fallback_poll_enabled=False,
        init_timeout_ms=10_000,
        context_upload_mode="none",
        collect_evaluation_summaries=False,
    )
    # init() blocks until the slow first fetch lands; run it off-thread so the
    # test can call into the client while init is genuinely in flight.
    threading.Thread(target=client.init, daemon=True, name="test-init").start()
    deadline = time.monotonic() + 2.0
    while not client._started and time.monotonic() < deadline:
        time.sleep(0.005)
    return client


def _timed(fn) -> Tuple[object, float]:  # type: ignore[no-untyped-def]
    start = time.monotonic()
    out = fn()
    return out, time.monotonic() - start


def test_should_log_logger_path_does_not_block_before_init(slow_server: str) -> None:
    client = _client(slow_server)
    try:
        allowed, elapsed = _timed(
            lambda: client.should_log(logger_path="a.b", desired_level="INFO")
        )
        assert elapsed < 0.5, f"should_log blocked {elapsed:.2f}s while init was in flight"
        assert allowed is True

        # Init completes once the slow fetch lands.
        assert client.wait_for_ready(timeout_ms=10_000)
        assert client.get_string("plain") == "ok"
        # After init the configured level (ERROR) applies.
        assert client.should_log(logger_path="a.b", desired_level="INFO") is False
        assert client.should_log(logger_path="a.b", desired_level="ERROR") is True
    finally:
        client.close()


def test_logging_filter_does_not_block_before_init(slow_server: str) -> None:
    client = _client(slow_server)
    try:
        record = logging.LogRecord("a.b", logging.INFO, __file__, 1, "msg", None, None)
        allowed, elapsed = _timed(lambda: QuonfigLoggerFilter(client).filter(record))
        assert elapsed < 0.5, f"filter blocked {elapsed:.2f}s while init was in flight"
        assert allowed is True
    finally:
        client.close()
