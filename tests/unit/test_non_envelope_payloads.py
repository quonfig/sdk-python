"""Non-envelope payloads + unversioned installs (qfg-9dxb.3, audit H2).

Two gaps the canonical-ordering spec never covered:

* Fix A — an UNVERSIONED install (``meta.generation`` <= 0/absent) still
  installs (the pre-watermark no-freeze carve-out), but it must never LOWER a
  positive held generation. Otherwise the next lower positive snapshot (a stale
  secondary) would be accepted and move an established client backward.
* Fix B — a payload that is not a config envelope (no ``meta`` object with a
  non-empty ``version``) is rejected. On HTTP it is a leg error, so hedge and
  failover proceed and the leg's ETag is never recorded; on SSE the event is
  dropped the same way malformed JSON is. api-delivery always sends
  ``meta.version`` + ``meta.environment``; ``qfg serve`` sends those without a
  generation and must keep installing via the carve-out.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Iterator, List, Optional
from unittest.mock import patch

import pytest

from quonfig import Quonfig
from quonfig.store import ConfigStore
from quonfig.transport import Transport
from quonfig.types import ConfigEnvelope, Meta


def _config(key: str) -> Dict[str, Any]:
    return {
        "id": f"id-{key}",
        "key": key,
        "type": "config",
        "valueType": "string",
        "default": {"rules": [{"criteria": [], "value": {"type": "string", "value": key}}]},
    }


def _envelope_json(generation: Optional[int], keys: List[str], version: str = "") -> bytes:
    meta: Dict[str, Any] = {
        "version": version or f"gen-{generation}",
        "environment": "Production",
    }
    if generation is not None:
        meta["generation"] = generation
    return json.dumps({"configs": [_config(k) for k in keys], "meta": meta}).encode()


class _Server:
    def __init__(self, handler: Callable[[BaseHTTPRequestHandler], None]) -> None:
        outer = handler

        class _H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer(self)

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        _host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()


def _write(req: BaseHTTPRequestHandler, body: bytes, etag: Optional[str] = None) -> None:
    req.send_response(200)
    if etag:
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


# --- Fix A: store-level ------------------------------------------------------


def _env(generation: int, version: str = "v") -> ConfigEnvelope:
    return ConfigEnvelope(
        configs=[],
        meta=Meta(version=f"{version}-{generation}", environment="p", generation=generation),
    )


def test_unversioned_install_keeps_held_generation_so_older_is_still_rejected() -> None:
    store = ConfigStore()
    assert store.update(_env(42), guard=True) is True
    # Carve-out: the unversioned payload still installs...
    assert store.update(_env(0, version="unversioned"), guard=True) is True
    # ...but the held watermark stays at the prior max,
    assert store.get_generation() == 42
    # so a stale lower positive snapshot can no longer move the client back.
    assert store.update(_env(41), guard=True) is False
    assert store.get_generation() == 42
    # A strictly newer snapshot still heals forward.
    assert store.update(_env(43), guard=True) is True
    assert store.get_generation() == 43


# --- Fix B: envelope validation ----------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"error": "x"},
        {"configs": []},
        {"configs": [], "meta": {}},
        {"configs": [], "meta": {"version": ""}},
        {"configs": [], "meta": "v1"},
        [],
        "nope",
    ],
)
def test_from_wire_rejects_non_envelopes(payload: Any) -> None:
    with pytest.raises(ValueError):
        ConfigEnvelope.from_wire(payload)


def test_from_wire_accepts_qfg_serve_payload_without_generation() -> None:
    env = ConfigEnvelope.from_wire(
        json.loads(_envelope_json(None, ["a"], version="serve-sha").decode())
    )
    assert env.meta.version == "serve-sha"
    assert env.meta.generation == 0
    assert [c.key for c in env.configs] == ["a"]


@pytest.mark.parametrize("junk", [b"{}", b'{"error":"x"}'])
def test_established_http_client_ignores_non_envelope_200(junk: bytes) -> None:
    state = {"junk": False, "etags_seen": []}  # type: Dict[str, Any]

    def handler(req: BaseHTTPRequestHandler) -> None:
        state["etags_seen"].append(req.headers.get("If-None-Match"))
        if state["junk"]:
            _write(req, junk, etag='"junk"')
            return
        _write(req, _envelope_json(42, ["a", "b"]), etag='"gen-42"')

    server = _Server(handler)
    client = _make_client([server.url])
    try:
        client.init()
        _await_ready(client)
        assert sorted(client.keys()) == ["a", "b"]
        assert client.held_generation() == 42
        installs = client.config_install_count()

        state["junk"] = True
        client.refresh()  # junk 200 → leg error, nothing installed

        assert sorted(client.keys()) == ["a", "b"], "a non-envelope 200 must not wipe keys"
        assert client.held_generation() == 42, "a non-envelope 200 must not lower held"
        assert client.config_install_count() == installs

        client.refresh()
        # The junk leg's ETag must never have been recorded (else later 304s
        # would pin the client to a payload it never installed).
        assert '"junk"' not in state["etags_seen"]
    finally:
        client.close()
        server.close()


def test_hedge_fails_over_past_non_envelope_primary() -> None:
    def primary_handler(req: BaseHTTPRequestHandler) -> None:
        _write(req, b"{}", etag='"junk"')

    def secondary_handler(req: BaseHTTPRequestHandler) -> None:
        _write(req, _envelope_json(7, ["from-secondary"]), etag='"gen-7"')

    primary = _Server(primary_handler)
    secondary = _Server(secondary_handler)
    client = _make_client([primary.url, secondary.url])
    try:
        client.init()
        _await_ready(client)
        assert client.keys() == ["from-secondary"]
        assert client.held_generation() == 7
    finally:
        client.close()
        primary.close()
        secondary.close()


def test_sequential_fetch_fails_over_past_non_envelope() -> None:
    primary = _Server(lambda req: _write(req, b'{"error":"x"}'))
    secondary = _Server(lambda req: _write(req, _envelope_json(3, ["s"])))
    transport = Transport(api_urls=[primary.url, secondary.url], sdk_key="sk")
    try:
        env = transport.fetch()
        assert env is not None
        assert env.meta.generation == 3
        assert transport.last_fetch_index == 1
    finally:
        transport.close()
        primary.close()
        secondary.close()


def test_established_http_client_installs_qfg_serve_payload_via_carve_out() -> None:
    state = {"serve": False}

    def handler(req: BaseHTTPRequestHandler) -> None:
        if state["serve"]:
            _write(req, _envelope_json(None, ["served"], version="serve-sha"), etag='"s"')
            return
        _write(req, _envelope_json(42, ["a"]), etag='"gen-42"')

    server = _Server(handler)
    client = _make_client([server.url])
    try:
        client.init()
        _await_ready(client)
        assert client.held_generation() == 42

        state["serve"] = True
        client.refresh()

        assert client.keys() == ["served"], "qfg serve payload must install (carve-out)"
        assert client.held_generation() == 42, "unversioned install keeps the prior max"
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


def test_sse_drops_non_envelope_events() -> None:
    from quonfig.sse import SSEClient

    transport = Transport(api_urls=["http://localhost:6550"], sdk_key="sk")
    store = ConfigStore()
    shutdown = threading.Event()
    installed: List[ConfigEnvelope] = []
    done = threading.Event()

    def install(env: ConfigEnvelope) -> bool:
        installed.append(env)
        return True

    def mock_events() -> Iterator[Any]:
        yield _FakeEvent("{}")
        yield _FakeEvent('{"error":"x"}')
        yield _FakeEvent(_envelope_json(None, ["served"], version="serve-sha").decode())
        shutdown.set()
        done.set()

    sse = SSEClient(transport, store, shutdown, install=install)
    with (
        patch("quonfig.sse.requests.get", return_value=_FakeResponse()),
        patch("quonfig.sse.sseclient.SSEClient") as mock_lib,
    ):
        mock_lib.return_value.events.side_effect = mock_events
        t = threading.Thread(target=sse._loop, daemon=True)
        t.start()
        done.wait(timeout=3.0)
        t.join(timeout=2.0)

    assert [e.meta.version for e in installed] == ["serve-sha"], (
        "non-envelope SSE events must be dropped; a qfg serve envelope installs"
    )
