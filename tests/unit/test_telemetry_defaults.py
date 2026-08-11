"""Regression tests for telemetry wiring in the Quonfig client.

Covers two bugs previously confirmed by live-fire:

1. The default telemetry URL must be ``https://telemetry.quonfig.com`` so
   events don't silently POST to ``api.quonfig.com`` and 404. This matches
   sdk-node / sdk-go. Domain-driven overrides (``QUONFIG_DOMAIN``) are
   covered by ``test_domain_env.py``.

2. Datadir-mode init must call ``TelemetryReporter.start()``. Previously
   only api-mode init started the reporter, so any datadir-backed service
   (e.g. api-delivery, api-telemetry) emitted zero telemetry.

3. Telemetry must be gated on SDK-key presence, not on mode (qfg-j001). A
   keyless client (the open-source / no-account datadir path) has no
   workspace to attribute events to, so the reporter must not be
   constructed at all. Previously it was, and every ``close()`` POSTed to
   the telemetry endpoint with ``Authorization: Basic base64("1:")``.
   Ported from sdk-node's ``test/datadir-telemetry.test.ts``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from quonfig import Quonfig


def _make_minimal_datadir(tmp_path: Path) -> str:
    """Create a workspace with one trivial config so load_datadir succeeds."""
    datadir = tmp_path / "workspace"
    datadir.mkdir()
    (datadir / "quonfig.json").write_text(
        json.dumps({"environments": ["Production"]}), encoding="utf-8"
    )
    configs_dir = datadir / "configs"
    configs_dir.mkdir()
    (configs_dir / "noop.json").write_text(
        json.dumps(
            {
                "id": "noop-id",
                "key": "noop",
                "type": "config",
                "valueType": "string",
                "sendToClientSdk": False,
                "default": {
                    "rules": [
                        {
                            "criteria": [{"operator": "ALWAYS_TRUE"}],
                            "value": {"type": "string", "value": "ok"},
                        }
                    ]
                },
                "environments": [
                    {
                        "id": "Production",
                        "rules": [
                            {
                                "criteria": [{"operator": "ALWAYS_TRUE"}],
                                "value": {"type": "string", "value": "ok"},
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(datadir)


class _TelemetryCapture:
    """A real HTTP server that records every telemetry POST it receives.

    Mirrors the live-server pattern in ``test_failover_telemetry.py`` — a
    mocked session would not prove the request never leaves the SDK, and the
    Authorization header is the thing that makes the bug visible.
    """

    def __init__(self) -> None:
        outer = self
        self.posts: list[str] = []
        self.auth_headers: list[str] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                outer.posts.append(self.rfile.read(length).decode())
                outer.auth_headers.append(self.headers.get("Authorization") or "")
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        _host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()


def test_datadir_without_sdk_key_sends_no_telemetry(tmp_path: Path, monkeypatch: Any) -> None:
    """Keyless datadir client must emit ZERO telemetry HTTP calls (qfg-j001)."""
    monkeypatch.delenv("QUONFIG_BACKEND_SDK_KEY", raising=False)
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    capture = _TelemetryCapture()
    datadir = _make_minimal_datadir(tmp_path)

    client = Quonfig(
        # No sdk_key — the datadir is the only credential-equivalent.
        datadir=datadir,
        environment="Production",
        telemetry_url=capture.url,
    )
    try:
        client.init()
        assert client.get("noop") == "ok"
        client.close()  # flushes pending telemetry, if any

        assert capture.posts == [], (
            "keyless datadir client POSTed telemetry "
            f"(auth headers {capture.auth_headers}): {capture.posts}"
        )
        assert client._telemetry is None, "the reporter must not be constructed without an sdk_key"
    finally:
        capture.close()


def test_datadir_with_sdk_key_still_sends_telemetry(tmp_path: Path, monkeypatch: Any) -> None:
    """The dogfood half: datadir + a real key must still flow (qfg-j001).

    Guards against over-rotating into sdk-ruby's inverse bug, where datadir
    mode drops telemetry even when a valid key is configured.
    """
    monkeypatch.delenv("QUONFIG_BACKEND_SDK_KEY", raising=False)
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    capture = _TelemetryCapture()
    datadir = _make_minimal_datadir(tmp_path)

    client = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        datadir=datadir,
        environment="Production",
        telemetry_url=capture.url,
    )
    try:
        client.init()
        assert client.get("noop") == "ok"
        client.close()  # flushes telemetry

        assert client._telemetry is not None
        assert capture.posts, "datadir client WITH an sdk_key sent no telemetry"
        assert any("noop" in body for body in capture.posts), (
            f"no telemetry POST carried the evaluated key: {capture.posts}"
        )
    finally:
        capture.close()


def test_default_telemetry_url_points_at_telemetry_host(monkeypatch: Any) -> None:
    # No env overrides — construction must default to telemetry.quonfig.com,
    # not api.quonfig.com. See sdk-node DEFAULT_TELEMETRY_URL.
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    client = Quonfig(sdk_key="qf_sk_development_0000_dead")
    assert client._telemetry_url == "https://telemetry.quonfig.com"


def test_explicit_telemetry_url_kwarg_wins(monkeypatch: Any) -> None:
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    client = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        telemetry_url="https://custom.example/",
    )
    assert client._telemetry_url == "https://custom.example/"


def test_datadir_mode_starts_telemetry_reporter(tmp_path: Path) -> None:
    datadir = _make_minimal_datadir(tmp_path)
    client = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        datadir=datadir,
        environment="Production",
    )
    client.init()

    # The reporter thread must exist and be alive after init — previously
    # _load_from_datadir never called start() and the thread was None.
    assert client._telemetry is not None, "telemetry should be constructed by default"
    thread = client._telemetry._thread
    assert thread is not None, "datadir init must call TelemetryReporter.start()"
    assert thread.is_alive(), "telemetry thread should be running after init"

    client.close()
