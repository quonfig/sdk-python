"""``Quonfig.close()`` closes the live SSE connection (qfg-goi1.2.13).

The SSE stream used a bare ``requests.get`` whose response nobody kept, and
``close()`` only set the shutdown flag. The SSE thread sat blocked in the read
until the next event/heartbeat (~30 s) or the 60 s read timeout, so every
closed client kept a delivery connection open that long (tests, per-tenant
create/close churn). ``close()`` now closes the live response, so the server
sees the socket close at once and the SSE thread exits.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import List, Optional

from quonfig import Quonfig

_ENVELOPE = json.dumps(
    {
        "configs": [],
        "meta": {"version": "gen-1", "environment": "Production", "generation": 1},
    }
).encode()


class _DeliveryServer:
    """Serves ``/api/v2/configs`` once per request, and holds every SSE stream
    open (headers plus one comment, then silence) until the client closes it,
    recording when that happens."""

    def __init__(self) -> None:
        self._ln = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ln.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ln.bind(("127.0.0.1", 0))
        self._ln.listen(16)
        self.sse_connected = threading.Event()
        self.sse_closed_at: Optional[float] = None
        self._conns: List[socket.socket] = []
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._ln.getsockname()[1]}"

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._ln.accept()
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            req = conn.recv(65536)
            if b"/sse/" in req.split(b"\r\n", 1)[0]:
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Cache-Control: no-cache\r\n\r\n: hello\n\n"
                )
                self.sse_connected.set()
                conn.settimeout(30)
                while conn.recv(4096):
                    pass
                self.sse_closed_at = time.monotonic()
                return
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(_ENVELOPE)}\r\nConnection: close\r\n\r\n".encode()
                + _ENVELOPE
            )
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        try:
            self._ln.close()
        except OSError:
            pass
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass


def test_close_closes_the_live_sse_connection() -> None:
    srv = _DeliveryServer()
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=[srv.url],
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        fallback_poll_enabled=False,
        enable_quonfig_user_context=False,
    )
    assert client._transport is not None
    client._transport._Transport__test_stream_url_override = (  # type: ignore[attr-defined]
        f"{srv.url}/api/v2/sse/config"
    )
    try:
        client.init()
        assert srv.sse_connected.wait(5), "SSE never connected"
        sse = client._sse
        assert sse is not None
        time.sleep(0.2)  # let the SSE thread block in its read

        closed_at = time.monotonic()
        client.close()

        deadline = closed_at + 2.0
        while srv.sse_closed_at is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert srv.sse_closed_at is not None, "SSE socket still open 2 s after close()"
        assert srv.sse_closed_at - closed_at < 1.0

        thread = sse._thread
        assert thread is not None
        thread.join(2.0)
        assert not thread.is_alive(), "SSE thread still running after close()"
    finally:
        client.close()
        srv.close()
