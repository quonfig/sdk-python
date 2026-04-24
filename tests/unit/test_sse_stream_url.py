"""Regression tests for SSE stream URL derivation in the Quonfig client.

The Python SDK must subscribe to the SSE endpoint on the *stream* host, not
the regular API host: `https://primary.quonfig.com/api/v2/sse/config` 404s,
but `https://stream.primary.quonfig.com/api/v2/sse/config` is the real SSE
endpoint. Other SDKs (sdk-node, sdk-ruby) derive the stream URL by prepending
`stream.` to the hostname — this test pins that behavior for sdk-python.
"""
from __future__ import annotations

import threading
from typing import Any, List
from unittest.mock import patch

import pytest

from quonfig.sse import SSEClient
from quonfig.store import ConfigStore
from quonfig.transport import Transport, derive_stream_url


class TestDeriveStreamUrl:
    def test_prepends_stream_to_production_host(self) -> None:
        assert (
            derive_stream_url("https://primary.quonfig.com")
            == "https://stream.primary.quonfig.com"
        )

    def test_prepends_stream_to_localhost_preserving_port(self) -> None:
        assert derive_stream_url("http://localhost:6550") == "http://stream.localhost:6550"

    def test_preserves_scheme_and_path(self) -> None:
        assert (
            derive_stream_url("http://api.example.com/base")
            == "http://stream.api.example.com/base"
        )


class _FakeResponse:
    def __init__(self) -> None:
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, *a: Any, **k: Any):
        return iter(())


def test_sse_client_subscribes_to_stream_host_not_api_host() -> None:
    """SSEClient must hit stream.<host>, not the plain api host — a 404-at-primary
    retry loop is the real-world bug this guards against."""
    transport = Transport(api_urls=["https://primary.quonfig.com"], sdk_key="sk")
    store = ConfigStore()
    shutdown = threading.Event()
    shutdown.set()  # make the loop exit on first pass

    sse_client = SSEClient(transport, store, shutdown)

    captured_urls: List[str] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured_urls.append(url)
        # Shut down so loop exits cleanly after one attempt
        shutdown.set()
        return _FakeResponse()

    with patch("quonfig.sse.requests.get", side_effect=fake_get), patch(
        "quonfig.sse.sseclient.SSEClient"
    ) as mock_sse_lib:
        mock_sse_lib.return_value.events.return_value = iter(())
        # _loop is a blocking method but shutdown is already set, so it exits fast
        shutdown.clear()
        sse_client._thread = threading.Thread(target=sse_client._loop, daemon=True)
        sse_client._thread.start()
        sse_client._thread.join(timeout=2.0)

    assert captured_urls, "SSEClient should have made at least one request"
    url = captured_urls[0]
    assert url.startswith("https://stream.primary.quonfig.com/"), (
        f"SSE URL must target stream.<host>, got: {url}"
    )
    assert url.endswith("/api/v2/sse/config")


def test_sse_url_method_on_transport() -> None:
    """Transport exposes the SSE URL so SSEClient doesn't duplicate logic."""
    t = Transport(api_urls=["https://primary.quonfig.com"], sdk_key="sk")
    assert t._current_stream_url() == "https://stream.primary.quonfig.com"
