"""Refresh-liveness stamp (qfg-41nh.11) — sdk-python side.

Mirrors sdk-go's refresh_liveness_test.go. ``last_successful_refresh()`` is a
LIVENESS signal, not an install counter: a config fetch that completes
successfully at the HTTP layer — 200 installed, 200 rejected by the reject-older
guard as equal-or-older, or 304 Not Modified — proves the source is reachable
and the held config current, so it must advance the stamp. Transport errors must
NOT. A received-and-processed SSE message stamps whether it installs or is a
guard no-op. Without this, a healthy long-lived client parked on 304s
under-reports liveness (the stamp freezes even though every fetch succeeds).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Iterator, List
from unittest.mock import patch

from quonfig import Quonfig


def _envelope_json(generation: int) -> bytes:
    return json.dumps(
        {
            "configs": [],
            "meta": {
                "version": f"gen-{generation}",
                "environment": "Production",
                "generation": generation,
            },
        }
    ).encode()


class _Server:
    """A tiny configurable httptest-style upstream. ``handler`` receives the
    ``BaseHTTPRequestHandler`` and writes the whole response."""

    def __init__(self, handler: Callable[[BaseHTTPRequestHandler], None]) -> None:
        outer = handler

        class _H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer(self)

            def log_message(self, *args: object) -> None:  # silence test server
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        _host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()


def _write_200(req: BaseHTTPRequestHandler, generation: int, etag: str) -> None:
    body = _envelope_json(generation)
    req.send_response(200)
    req.send_header("ETag", etag)
    req.send_header("Content-Type", "application/json")
    req.send_header("Content-Length", str(len(body)))
    req.end_headers()
    req.wfile.write(body)


def _make_client(api_urls: List[str]) -> Quonfig:
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=api_urls,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        fallback_poll_enabled=False,
        init_timeout_ms=8000,
        on_init_failure="return_zero_value",
    )
    # Point SSE at a dead port so it never connects — isolate the HTTP fetch path.
    if client._transport is not None:
        client._transport._Transport__test_stream_url_override = (  # type: ignore[attr-defined]
            "http://127.0.0.1:1/api/v2/sse/config"
        )
    return client


def _await_ready(client: Quonfig, within: float = 6.0) -> None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if client.ready():
            return
        time.sleep(0.02)
    raise AssertionError("client did not become ready in time")


def test_stamps_on_init_and_304() -> None:
    """The init install stamps, and a later refresh answered 304 Not Modified
    stamps again — the fetch succeeded and the held config was confirmed
    current — without re-installing or moving the held generation."""

    def handler(req: BaseHTTPRequestHandler) -> None:
        if req.headers.get("If-None-Match") == '"gen-42"':
            req.send_response(304)
            req.end_headers()
            return
        _write_200(req, 42, '"gen-42"')

    server = _Server(handler)
    client = _make_client([server.url])
    try:
        client.init()
        _await_ready(client)
        first = client.last_successful_refresh()
        assert first is not None
        installs = client.config_install_count()

        time.sleep(0.01)
        client.refresh()  # answered 304 Not Modified

        second = client.last_successful_refresh()
        assert second is not None
        assert second > first, f"a 304 IS a successful refresh: {second} !> {first}"
        assert client.config_install_count() == installs, "304 must not re-install"
        assert client.held_generation() == 42, "304 must not touch the held config"
    finally:
        client.close()
        server.close()


def test_stamps_on_guard_rejected_200() -> None:
    """An established client fails over to a leg serving an OLDER generation.
    The reject-older guard drops the payload — but the fetch itself succeeded,
    so liveness must still advance (no install, held generation unchanged)."""
    primary_dead = {"v": False}

    def primary_handler(req: BaseHTTPRequestHandler) -> None:
        if primary_dead["v"]:
            req.send_response(503)
            req.end_headers()
            req.wfile.write(b"primary refused")
            return
        _write_200(req, 42, '"gen-42"')

    # The secondary varies its ETag per request so every fetch is a full 200
    # (never a 304): isolate the guard-rejected path.
    sec_req = {"n": 0}

    def secondary_handler(req: BaseHTTPRequestHandler) -> None:
        sec_req["n"] += 1
        _write_200(req, 41, f'"gen-41-{sec_req["n"]}"')

    primary = _Server(primary_handler)
    secondary = _Server(secondary_handler)
    client = _make_client([primary.url, secondary.url])
    try:
        client.init()
        _await_ready(client)
        assert client.held_generation() == 42
        installs = client.config_install_count()
        first = client.last_successful_refresh()
        assert first is not None

        primary_dead["v"] = True
        time.sleep(0.01)
        client.refresh()  # fails over to secondary gen 41 → guard-rejected

        second = client.last_successful_refresh()
        assert second is not None
        assert second > first, (
            f"guard-rejected 200 must advance liveness (fetch succeeded): {second} !> {first}"
        )
        assert client.config_install_count() == installs, "the stamp must not come from an install"
        assert client.held_generation() == 42, "guard must still reject the older payload"
    finally:
        client.close()
        primary.close()
        secondary.close()


def test_not_stamped_on_error() -> None:
    """A refresh whose every leg fails must NOT advance the stamp — the whole
    point is distinguishing 'confirmed current' from 'cannot reach the
    source'."""
    dead = {"v": False}

    def handler(req: BaseHTTPRequestHandler) -> None:
        if dead["v"]:
            req.send_response(503)
            req.end_headers()
            req.wfile.write(b"gone")
            return
        _write_200(req, 42, '"gen-42"')

    server = _Server(handler)
    client = _make_client([server.url])
    try:
        client.init()
        _await_ready(client)
        first = client.last_successful_refresh()
        assert first is not None

        dead["v"] = True
        time.sleep(0.01)
        client.refresh()  # every leg fails → nothing to stamp

        assert client.last_successful_refresh() == first, "errors must not stamp"
    finally:
        client.close()
        server.close()


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, *_a: Any, **_k: Any) -> Iterator[bytes]:
        return iter(())


class _FakeEvent:
    def __init__(self, data: str) -> None:
        self.data = data


def test_sse_message_stamps_on_install_and_on_guard_no_op() -> None:
    """A received-and-processed SSE message counts as a successful refresh
    whether it installs or is guard-no-op'd. An accepted install stamps via
    ``on_config_update``; a guard no-op stamps via ``record_refresh`` — so an
    install never double-stamps."""
    from quonfig.sse import SSEClient
    from quonfig.store import ConfigStore
    from quonfig.transport import Transport

    transport = Transport(api_urls=["http://localhost:6550"], sdk_key="sk")
    store = ConfigStore()
    shutdown = threading.Event()

    update_calls = {"n": 0}
    refresh_calls = {"n": 0}
    # First message installs (True), second is guard-rejected (False).
    install_outcomes = iter([True, False])
    done = threading.Event()

    def install(_env: object) -> bool:
        return next(install_outcomes)

    def mock_events() -> Iterator[Any]:
        payload = _envelope_json(42).decode()
        yield _FakeEvent(payload)  # installs → on_config_update
        yield _FakeEvent(payload)  # guard no-op → record_refresh
        shutdown.set()
        done.set()

    sse = SSEClient(
        transport,
        store,
        shutdown,
        on_config_update=lambda: update_calls.__setitem__("n", update_calls["n"] + 1),
        install=install,
        record_refresh=lambda: refresh_calls.__setitem__("n", refresh_calls["n"] + 1),
    )

    with (
        patch("quonfig.sse.requests.get", return_value=_FakeResponse()),
        patch("quonfig.sse.sseclient.SSEClient") as mock_lib,
    ):
        mock_lib.return_value.events.side_effect = mock_events
        t = threading.Thread(target=sse._loop, daemon=True)
        t.start()
        done.wait(timeout=3.0)
        t.join(timeout=2.0)

    assert update_calls["n"] == 1, "an accepted SSE install must fire on_config_update"
    assert refresh_calls["n"] == 1, (
        "a guard-no-op SSE message must advance liveness via record_refresh"
    )
