"""Mid-body read failures settle the leg instead of escaping it (qfg-goi1.2.13).

The config body is drained through ``response.raw.read1()``, which raises
**urllib3** exceptions (``ProtocolError`` for a truncated/reset body,
``ReadTimeoutError`` for a stalled one) that ``requests`` does not wrap on that
path. Pre-fix, ``_fetch_leg`` and the sequential ``fetch()`` caught only
``requests.*`` exceptions, so:

- a hedged leg thread died without ever putting a ``LegResult``, and
  ``fetch_hedged`` sat out its whole drain budget (~15 s by default) waiting
  for it;
- the sequential ``fetch()`` let the exception escape instead of failing over
  to the next URL.

Every failure of a leg is a leg error: these tests pin that a truncating or
stalling upstream yields an error ``LegResult`` promptly, and that the
sequential path fails over.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

from quonfig.transport import LegResult, Transport


def _envelope_json(generation: int) -> bytes:
    return json.dumps(
        {
            "configs": [],
            "meta": {
                "version": f"gen-{generation}",
                "environment": "production",
                "generation": generation,
            },
        }
    ).encode()


class _GoodServer:
    def __init__(self, generation: int) -> None:
        body = _envelope_json(generation)

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()


class _BrokenBodyServer:
    """Answers 200 with ``Content-Length: 1000`` and a partial body, then either
    closes the socket (``truncate``: urllib3 ``ProtocolError``) or holds it open
    without sending more (``stall``: urllib3 ``ReadTimeoutError``)."""

    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._ln = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ln.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ln.bind(("127.0.0.1", 0))
        self._ln.listen(16)
        self._stop = threading.Event()
        self._conns: List[socket.socket] = []
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._ln.getsockname()[1]}"

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._ln.accept()
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b'Content-Length: 1000\r\n\r\n{"meta":'
            )
            if self._mode == "truncate":
                conn.close()
                return
            self._stop.wait(30)
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._ln.close()
        except OSError:
            pass
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass


def test_fetch_leg_truncated_body_returns_error_leg_result() -> None:
    srv = _BrokenBodyServer("truncate")
    try:
        t = Transport([srv.url], "k", hedge_abort=1.0)
        lr = t._fetch_leg(0, 1.0)
        assert isinstance(lr, LegResult)
        assert lr.source_index == 0
        assert lr.error is not None
        assert lr.envelope is None
    finally:
        srv.close()


def test_fetch_leg_stalled_body_returns_error_leg_result() -> None:
    srv = _BrokenBodyServer("stall")
    try:
        t = Transport([srv.url], "k", hedge_abort=0.5)
        t0 = time.monotonic()
        lr = t._fetch_leg(0, 0.5)
        elapsed = time.monotonic() - t0
        assert lr.error is not None
        assert lr.envelope is None
        assert elapsed < 2.0, f"stalled leg took {elapsed:.2f}s to settle"
    finally:
        srv.close()


def test_fetch_hedged_truncated_single_leg_settles_promptly() -> None:
    """Pre-fix the leg thread died and the drain waited
    ``delay + 2*abort + 1`` for a result that never came."""
    srv = _BrokenBodyServer("truncate")
    try:
        t = Transport([srv.url], "k", hedge_delay=0.2, hedge_abort=2.0)
        t0 = time.monotonic()
        results = list(t.fetch_hedged())
        elapsed = time.monotonic() - t0
        assert len(results) == 1
        assert results[0].error is not None
        assert elapsed < 1.5, f"hedge took {elapsed:.2f}s (drain budget waited out)"
    finally:
        srv.close()


def test_fetch_hedged_truncated_primary_hedges_to_healthy_secondary() -> None:
    bad = _BrokenBodyServer("truncate")
    good = _GoodServer(generation=7)
    try:
        t = Transport([bad.url, good.url], "k", hedge_delay=1.0, hedge_abort=2.0)
        t0 = time.monotonic()
        results = list(t.fetch_hedged())
        elapsed = time.monotonic() - t0
        by_idx = {r.source_index: r for r in results}
        assert by_idx[0].error is not None
        assert by_idx[1].envelope is not None
        assert by_idx[1].envelope.meta.generation == 7
        assert elapsed < 1.5, f"hedge took {elapsed:.2f}s"
    finally:
        bad.close()
        good.close()


def test_fetch_hedged_leg_that_raises_still_settles() -> None:
    """Defense in depth: even if the leg function itself raises, the hedge
    worker still delivers an error LegResult so the drain never waits it out."""
    t = Transport(["http://127.0.0.1:1"], "k", hedge_delay=0.2, hedge_abort=2.0)

    def _boom(idx: int, abort: float) -> LegResult:
        raise RuntimeError("leg blew up")

    t._fetch_leg = _boom  # type: ignore[method-assign]
    t0 = time.monotonic()
    results = list(t.fetch_hedged())
    elapsed = time.monotonic() - t0
    assert len(results) == 1
    assert isinstance(results[0].error, RuntimeError)
    assert results[0].source_index == 0
    assert elapsed < 1.5, f"hedge took {elapsed:.2f}s"


def test_sequential_fetch_truncated_primary_fails_over() -> None:
    bad = _BrokenBodyServer("truncate")
    good = _GoodServer(generation=9)
    try:
        t = Transport([bad.url, good.url], "k", timeout=2.0)
        env = t.fetch()
        assert env is not None
        assert env.meta.generation == 9
        assert t.last_fetch_index == 1
    finally:
        bad.close()
        good.close()


def test_sequential_fetch_stalled_primary_fails_over() -> None:
    bad = _BrokenBodyServer("stall")
    good = _GoodServer(generation=11)
    try:
        t = Transport([bad.url, good.url], "k", timeout=0.5)
        env = t.fetch()
        assert env is not None
        assert env.meta.generation == 11
    finally:
        bad.close()
        good.close()
