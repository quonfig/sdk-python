"""Per-URL config-fetch timeout for fast hang failover (qfg-7h5d.1.8).

Mirrors sdk-go's TestFetchConfigsPerURLTimeoutFailsOver. A hung primary (accepts
the TCP connection but never responds) must abort fast — bounded by the per-URL
config-fetch timeout (~3s default) — so the secondary is reached well inside the
overall init budget instead of being starved until it.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from quonfig.transport import Transport

_SECONDARY_BODY = {
    "configs": [],
    "meta": {"version": "sec-7", "environment": "production", "generation": 7},
}


class _GoodHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(_SECONDARY_BODY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence test server logs
        pass


def _start_good_server() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _GoodHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://127.0.0.1:{port}"


def _start_hung_listener() -> tuple[socket.socket, str]:
    """A TCP listener that accepts connections but never replies — the 'hang'."""
    ln = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ln.bind(("127.0.0.1", 0))
    ln.listen(8)
    accepted: list[socket.socket] = []

    def _accept_loop() -> None:
        while True:
            try:
                conn, _ = ln.accept()
            except OSError:
                return
            accepted.append(conn)  # hold the connection open, never respond

    threading.Thread(target=_accept_loop, daemon=True).start()
    host, port = ln.getsockname()
    return ln, f"http://127.0.0.1:{port}"


def test_default_per_url_fetch_timeout_is_about_3s() -> None:
    """The per-URL deadline defaults to ~3s — short enough that a hung primary
    fails over inside a default init budget."""
    t = Transport(api_urls=["http://example.invalid"], sdk_key="k")
    assert t.timeout == 3.0


def test_hung_primary_fails_over_to_secondary_within_budget() -> None:
    good_server, secondary_url = _start_good_server()
    hung_ln, primary_url = _start_hung_listener()
    try:
        transport = Transport(api_urls=[primary_url, secondary_url], sdk_key="k")
        start = time.monotonic()
        envelope = transport.fetch()
        elapsed = time.monotonic() - start

        assert envelope is not None
        assert envelope.meta.generation == 7
        # Per-URL timeout (~3s) bounds the hung primary; failover then reaches
        # the secondary inside a ~4s budget. Without the per-URL timeout the
        # default 10s request deadline would blow past this.
        assert elapsed < 4.0, f"failover took {elapsed:.1f}s — primary hang was not bounded"
        # And the leg that produced the held config is the secondary (index 1).
        assert transport.last_fetch_index == 1
    finally:
        transport.close()
        good_server.shutdown()
        hung_ln.close()
