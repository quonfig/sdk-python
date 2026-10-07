"""Regression test for qfg-goi1.2.12: SSE clean-EOF reconnect storm.

A server that answers ``200 text/event-stream`` and then closes the socket
cleanly (no events) made ``SSEClient._loop`` reconnect with no delay at all:
the clean-EOF path took a transparent reconnect and looped straight back into
``requests.get``. The audit repro saw ~3000 connections in 3 s, and every
reconnect re-emitted ``connected``.

The fix mirrors sdk-go (``sse_client.go`` runLoop): a jittered sleep of
``delay/2 .. delay`` (``delay`` = 0.5 s) before every reconnect, a clean EOF
included, with no backoff growth on clean EOF. A transparent reconnect does
not re-emit ``connected`` either: the public state never left ``connected``.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import List

from quonfig.sse import SSEClient
from quonfig.store import ConfigStore
from quonfig.transport import Transport


def _start_eof_server() -> tuple[socket.socket, dict]:
    """A socket server that answers 200 event-stream and closes immediately."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    stats = {"count": 0}

    def serve() -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            stats["count"] += 1
            try:
                conn.recv(4096)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return sock, stats


def test_clean_eof_reconnects_with_a_floor_and_no_repeated_connected() -> None:
    sock, stats = _start_eof_server()
    port = sock.getsockname()[1]
    try:
        transport = Transport([f"http://127.0.0.1:{port}"], "sk")
        shutdown = threading.Event()
        states: List[str] = []
        lock = threading.Lock()

        def listener(s: str) -> None:
            with lock:
                states.append(s)

        sse = SSEClient(transport, ConfigStore(), shutdown, state_listener=listener)
        sse._stream_url_override = f"http://127.0.0.1:{port}/api/v2/sse/config"
        sse.start()
        time.sleep(3.0)
        shutdown.set()
        assert sse._thread is not None
        sse._thread.join(timeout=2.0)

        count = stats["count"]
        # Floor of >= 0.25 s per reconnect: at most ~12 connections in 3 s.
        # Before the fix this was in the thousands.
        assert count <= 12, f"clean-EOF reconnect storm: {count} connections in 3 s"
        # It must still reconnect (the floor is a delay, not a stop).
        assert count >= 3, f"expected the client to keep reconnecting; got {count}"
        with lock:
            seen = list(states)
        # Exactly one connecting/connected pair, then the shutdown edge. No
        # repeated 'connected' on each transparent reconnect.
        assert seen[:2] == ["connecting", "connected"], seen
        assert seen.count("connected") == 1, (
            f"transparent reconnect re-emitted 'connected': {seen[:8]} ... total {len(seen)}"
        )
        assert "connecting" not in seen[1:], seen
        assert "error" not in seen, seen
    finally:
        sock.close()
