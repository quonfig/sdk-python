"""Wall-clock per-leg abort for slow-drip upstreams (qfg-41nh.10, WS2.4).

A scalar ``requests`` timeout only bounds connect + BETWEEN-BYTES gaps — a
slow-drip upstream (sends 1 byte before every timeout tick, never finishes the
body) resets the read timer forever and the leg never aborts. Pre-fix, that
wedged ``fetch_hedged``'s untimed ``out.get()`` drain and therefore the
fallback-poll loop that drives it: with SSE already down, BOTH refresh layers
were dead with zero signal.

These tests pin true wall-clock behavior: every leg (hedged and sequential)
must settle within its per-URL budget even when the upstream keeps dripping
bytes, and the hedge drain must never outlive the per-leg deadlines.

RED baseline (pre-fix): each test's worker thread is still wedged on the drip
leg when the wall-clock bound expires, so the "settled within" assertions fail.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List, Optional

from quonfig.transport import LegResult, Transport
from quonfig.types import ConfigEnvelope


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
    """A fast healthy upstream pinned to a Meta.generation."""

    def __init__(self, generation: int) -> None:
        body = _envelope_json(generation)

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:  # silence test server
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()


class _DripServer:
    """The slow-drip pathology: answers with valid 200 headers and a huge
    Content-Length, then drips one body byte every ``pace`` seconds forever.

    Every drip arrives well inside a between-bytes read timeout, so a scalar
    ``requests`` timeout NEVER fires — only a true wall-clock deadline can
    abort this leg.
    """

    def __init__(self, pace: float = 0.15) -> None:
        self._pace = pace
        self._ln = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ln.bind(("127.0.0.1", 0))
        self._ln.listen(8)
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
            threading.Thread(target=self._drip, args=(conn,), daemon=True).start()

    def _drip(self, conn: socket.socket) -> None:
        try:
            conn.recv(65536)  # consume the request head
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 10000000\r\n\r\n"
            )
            while True:
                conn.sendall(b"x")
                time.sleep(self._pace)
        except OSError:
            return  # client aborted the leg — expected

    def close(self) -> None:
        self._ln.close()
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass


def _run_bounded(target, bound: float):  # type: ignore[no-untyped-def]
    """Run ``target`` on a worker thread; return (finished, result, elapsed).

    The worker is what pre-fix wedges forever — running it directly would hang
    the test session, so the RED assertion is 'did not settle within bound'.
    """
    result: dict = {}

    def _work() -> None:
        result["value"] = target()

    start = time.monotonic()
    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(bound)
    return not worker.is_alive(), result.get("value"), time.monotonic() - start


def test_fetch_leg_aborts_slow_drip_within_wallclock_deadline() -> None:
    """A hedged leg against a drip upstream must settle (as an error) within
    ~2x its abort budget — deadline elapse + at most one more between-bytes
    read — instead of following the drip forever."""
    drip = _DripServer()
    transport = Transport(api_urls=[drip.url], sdk_key="k")
    try:
        finished, leg, elapsed = _run_bounded(lambda: transport._fetch_leg(0, 1.0), bound=3.0)
        assert finished, (
            f"_fetch_leg still wedged after {elapsed:.1f}s against a slow-drip "
            "upstream — the per-leg abort is not wall-clock"
        )
        assert isinstance(leg, LegResult)
        assert leg.error is not None, "drip leg must settle as an error, not a success"
    finally:
        transport.close()
        drip.close()


def test_hedged_drain_not_wedged_by_slow_drip_primary() -> None:
    """fetch_hedged's drain must yield the fast secondary AND finish within the
    per-leg deadlines even while the primary drips forever. Pre-fix the final
    untimed ``out.get()`` waits for the wedged primary leg indefinitely —
    exactly the state that killed the fallback-poll loop."""
    drip = _DripServer()
    good = _GoodServer(generation=7)
    transport = Transport(api_urls=[drip.url, good.url], sdk_key="k")
    try:

        def _drain() -> List[LegResult]:
            return list(transport.fetch_hedged(hedge_delay=0.2, hedge_abort=1.0))

        finished, legs, elapsed = _run_bounded(_drain, bound=5.0)
        assert finished, (
            f"fetch_hedged drain still wedged after {elapsed:.1f}s — a slow-drip "
            "primary must not block the drain past the per-leg deadlines"
        )
        assert legs is not None
        secondary_ok: Optional[ConfigEnvelope] = next(
            (lr.envelope for lr in legs if lr.source_index == 1 and lr.envelope), None
        )
        assert secondary_ok is not None, "fast secondary's envelope must be yielded"
        assert secondary_ok.meta.generation == 7
    finally:
        transport.close()
        drip.close()
        good.close()


def test_sequential_fetch_fails_over_past_slow_drip_primary() -> None:
    """The sequential ``fetch`` path (legacy fallback poll) has the same flaw:
    a drip primary must abort at the per-URL budget and fail over to the
    secondary instead of following the drip forever."""
    drip = _DripServer()
    good = _GoodServer(generation=9)
    transport = Transport(api_urls=[drip.url, good.url], sdk_key="k", timeout=1.0)
    try:
        finished, envelope, elapsed = _run_bounded(transport.fetch, bound=4.0)
        assert finished, (
            f"Transport.fetch still wedged after {elapsed:.1f}s — the sequential "
            "per-URL timeout is not wall-clock"
        )
        assert envelope is not None
        assert envelope.meta.generation == 9
        assert transport.last_fetch_index == 1
    finally:
        transport.close()
        drip.close()
        good.close()
