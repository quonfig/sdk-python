"""Size caps (supervisor contract "Rule — size caps" + Test 10; qfg-6vno.20.4).

- One SSE event is capped at 4 MiB (4,194,304 bytes). Over the cap it is not a
  frame: the connection is a failed attempt (backoff grows, never
  ``connected``, the fallback poller engages), one WARN per oversized episode
  naming the cap, and a keepalive alone does not clear the episode.
- An HTTP 200 body from ``/api/v2/configs`` is capped at 16 MiB (16,777,216
  bytes). Over the cap it is a failed leg exactly like a 5xx: nothing
  installed, no ETag stored, one WARN per episode.
- Buffering stays bounded near the cap, even for an event with no newline,
  and an unterminated tail is not rescanned on every chunk (the old parser
  re-ran ``splitlines`` over the whole carried tail per chunk: quadratic).

Same local-server fixture as Test 9 (``test_supervisor.T9``).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterator, List, Optional
from unittest.mock import MagicMock

import pytest

from quonfig.sse import MAX_SSE_EVENT_BYTES, SSEClient
from quonfig.transport import MAX_CONFIG_BODY_BYTES, Transport
from tests.unit.test_supervisor import (
    T9,
    T9_KEY,
    check_sse_ladder,
    sse_end,
    sse_open,
    sse_write,
    t9_envelope,
    wait_for,
)

MiB = 1024 * 1024


@pytest.fixture
def t9() -> Iterator[Callable[..., T9]]:
    made: List[T9] = []

    def make(body: Callable[[T9, BaseHTTPRequestHandler, int], None]) -> T9:
        x = T9(body=body)
        made.append(x)
        return x

    yield make
    for x in made:
        x.stop()


def test_caps_are_the_contract_constants() -> None:
    assert MAX_SSE_EVENT_BYTES == 4_194_304
    assert MAX_CONFIG_BODY_BYTES == 16_777_216


def padded_envelope(gen: int, value: str, size: int) -> bytes:
    """A valid envelope whose JSON is exactly ``size`` bytes."""
    base = t9_envelope(gen, value)
    doc = json.loads(base)
    doc["meta"]["pad"] = ""
    probe = json.dumps(doc).encode()
    pad = size - len(probe)
    assert pad >= 0
    doc["meta"]["pad"] = "x" * pad
    out = json.dumps(doc).encode()
    assert len(out) == size
    return out


def write_big(h: BaseHTTPRequestHandler, data: bytes, piece: int = 65536) -> None:
    mv = memoryview(data)
    for i in range(0, len(data), piece):
        sse_write(h, bytes(mv[i : i + piece]))


@pytest.fixture
def warn_records() -> Iterator[List[logging.LogRecord]]:
    records: List[logging.LogRecord] = []

    class H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    h = H(level=logging.DEBUG)
    qlog = logging.getLogger("quonfig")
    old = qlog.level
    qlog.setLevel(logging.DEBUG)
    qlog.addHandler(h)
    yield records
    qlog.removeHandler(h)
    qlog.setLevel(old)


def warns_with(records: List[logging.LogRecord], needle: str) -> List[str]:
    return [
        r.getMessage()
        for r in list(records)
        if r.levelno == logging.WARNING and needle in r.getMessage()
    ]


# -- SSE ----------------------------------------------------------------------


def test_size_caps_10_1_oversized_sse_event_backs_off_and_poller_takes_over(
    t9: Callable[..., T9], warn_records: List[logging.LogRecord]
) -> None:
    """10.1: SSE serves ``id: g2`` + a valid envelope padded to 4 MiB + 1 and
    holds. Never connected, >= 2 connects with growing gaps, the poller
    fetched, exactly one WARN naming 4194304."""
    big = padded_envelope(2, "too-big", MAX_SSE_EVENT_BYTES + 1)

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        sse_write(h, b"id: g2\n")
        write_big(h, b"data: " + big + b"\n\n")
        x.hold.wait(10)

    x = t9(body)
    c = x.start(poll_interval_s=0.5)
    assert wait_for(lambda: x.attempts >= 2 and x.config_hits >= 2, 10.0), (
        f"attempts={x.attempts} config_hits={x.config_hits} states={x.states()}"
    )
    assert x.connected_edges() == [], x.states()
    assert not x.ever_connected
    assert c.get(T9_KEY) == "v2", "fallback poller did not deliver"
    with x.lock:
        w = list(x.sse_waits)
    assert len(w) >= 1
    check_sse_ladder(w, "10.1")
    assert len(warns_with(warn_records, "4194304")) == 1, [
        r.getMessage() for r in warn_records if r.levelno >= logging.WARNING
    ]


def test_size_caps_10_2_no_newline_8mib_ends_and_retries_bounded(
    t9: Callable[..., T9],
) -> None:
    """10.2: ``data: `` + 8 MiB with no newline, stream held. The SDK ends the
    connection itself (stops reading at the cap) and retries with backoff;
    buffering for it stays near the cap."""
    blob = b"data: " + b"y" * (8 * MiB)

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        try:
            write_big(h, blob)
        except OSError:
            return
        x.hold.wait(10)

    x = t9(body)
    tracemalloc.start()
    try:
        x.start(poll_interval_s=5.0)
        tracemalloc.reset_peak()
        assert wait_for(lambda: x.attempts >= 2, 8.0), f"attempts={x.attempts} {x.states()}"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert not x.ever_connected
    with x.lock:
        w = list(x.sse_waits)
    assert w, "no backoff sleep"
    # The server side of this test allocates its 64 KiB pieces in-process too;
    # an SDK that buffered the whole 8 MiB line would blow well past this.
    assert peak < 7 * MiB, f"peak traced memory {peak / MiB:.1f} MiB"


def test_size_caps_keepalive_alone_does_not_clear_the_oversized_episode(
    t9: Callable[..., T9], warn_records: List[logging.LogRecord]
) -> None:
    """After an oversized event, a keepalive-only connection is not
    ``connected``; the first envelope ends the episode."""
    big = padded_envelope(2, "too-big", MAX_SSE_EVENT_BYTES + 1)

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        if n == 1:
            write_big(h, b"data: " + big + b"\n\n")
            x.hold.wait(10)
        elif n == 2:
            sse_write(h, b": keepalive\n\n")
            time.sleep(0.8)
            sse_end(h)
        else:
            sse_write(h, b"data: " + t9_envelope(3, "v3") + b"\n\n")
            x.hold.wait(10)

    x = t9(body)
    c = x.start(poll_interval_s=60.0)
    assert wait_for(lambda: x.attempts >= 2, 5.0), x.states()
    time.sleep(0.4)
    assert not x.ever_connected, f"keepalive cleared the oversized gate: {x.states()}"
    assert wait_for(lambda: c.get(T9_KEY) == "v3", 10.0), x.states()
    assert wait_for(lambda: c.connection_state() == "connected", 2.0), x.states()
    assert len(warns_with(warn_records, "4194304")) == 1


def test_size_caps_10_5_control_3mib_sse_event_installs(t9: Callable[..., T9]) -> None:
    """10.5 (SSE half): a 3 MiB envelope over SSE installs."""
    env = padded_envelope(2, "three-mib", 3 * MiB)

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        write_big(h, b"data: " + env + b"\n\n")
        x.hold.wait(10)

    x = t9(body)
    c = x.start(poll_interval_s=60.0)
    assert wait_for(lambda: c.get(T9_KEY) == "three-mib", 10.0), x.states()
    assert c.connection_state() == "connected"


def _parser(installed: List[object]) -> SSEClient:
    def install(env: object) -> bool:
        installed.append(env)
        return True

    p = SSEClient(MagicMock(), MagicMock(), threading.Event(), install=install)
    p._stream_url_override = "http://127.0.0.1:1"
    return p


def test_sse_unterminated_tail_is_not_rescanned_per_chunk() -> None:
    """A near-cap single line delivered in 1 KiB chunks parses in linear time.
    The old parser re-split the whole carried tail on every chunk."""
    installed: List[object] = []
    env = padded_envelope(2, "linear", 3 * MiB)
    data = b"data: " + env + b"\n\n"
    chunks = [data[i : i + 1024] for i in range(0, len(data), 1024)]
    p = _parser(installed)
    t0 = time.monotonic()
    p._parse_stream(iter(chunks), announce=False)
    elapsed = time.monotonic() - t0
    assert len(installed) == 1
    assert elapsed < 2.0, f"parse took {elapsed:.2f}s: tail rescanned per chunk"


def test_sse_multiline_data_sum_is_capped() -> None:
    """Many small ``data:`` lines whose sum passes the cap are oversized too."""
    installed: List[object] = []
    line = b"data: " + b"z" * 1000 + b"\n"
    n = MAX_SSE_EVENT_BYTES // len(line) + 2
    p = _parser(installed)
    with pytest.raises(Exception, match="4194304"):
        p._parse_stream(iter([line] * n + [b"\n"]), announce=False)
    assert installed == []


def test_sse_parser_still_handles_crlf_and_split_terminators() -> None:
    installed: List[object] = []
    env = t9_envelope(2, "crlf")
    data = b": keepalive\r\n\r\ndata: " + env + b"\r\n\r\n"
    chunks = [data[i : i + 7] for i in range(0, len(data), 7)]
    p = _parser(installed)
    assert p._parse_stream(iter(chunks), announce=False) is True
    assert len(installed) == 1


# -- HTTP ---------------------------------------------------------------------


class _BodyServer:
    """Serves ``/api/v2/configs`` with a body chosen per request; records the
    ``If-None-Match`` it saw."""

    def __init__(self, body_for: Callable[[int], bytes], content_length: bool) -> None:
        self.seen_inm: List[Optional[str]] = []
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                outer.hits += 1
                outer.seen_inm.append(self.headers.get("If-None-Match"))
                body = body_for(outer.hits)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("ETag", f'"etag-{outer.hits}"')
                self.send_header("Connection", "close")
                try:
                    if content_length:
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        mv = memoryview(body)
                        for i in range(0, len(body), 65536):
                            piece = bytes(mv[i : i + 65536])
                            self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
                        self.wfile.write(b"0\r\n\r\n")
                except OSError:
                    pass

            def log_message(self, *args: object) -> None:
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture
def body_server() -> Iterator[Callable[..., _BodyServer]]:
    made: List[_BodyServer] = []

    def make(body_for: Callable[[int], bytes], content_length: bool) -> _BodyServer:
        s = _BodyServer(body_for, content_length)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def _transport(url: str) -> Transport:
    return Transport(api_urls=[url], sdk_key="k", timeout=20.0)


@pytest.mark.parametrize("content_length", [True, False], ids=["content-length", "chunked"])
def test_size_caps_10_3_oversized_body_is_a_failed_leg_no_etag(
    body_server: Callable[..., _BodyServer],
    warn_records: List[logging.LogRecord],
    content_length: bool,
) -> None:
    """10.3: a 16 MiB + 1 byte 200 body fails the leg, installs nothing and
    stores no ETag; the next request sends no If-None-Match from it. One WARN
    naming 16777216 for the episode."""
    big = padded_envelope(2, "too-big", MAX_CONFIG_BODY_BYTES + 1)
    srv = body_server(lambda n: big, content_length)
    tr = _transport(srv.url)

    r1 = tr._fetch_leg(0, abort=20.0)
    assert r1.envelope is None and r1.error is not None, r1
    assert "16777216" in str(r1.error)
    r2 = tr._fetch_leg(0, abort=20.0)
    assert r2.envelope is None and r2.error is not None
    assert srv.seen_inm == [None, None], srv.seen_inm
    assert tr._etags == {}

    with pytest.raises(RuntimeError, match="16777216"):
        tr.fetch()
    assert len(warns_with(warn_records, "16777216")) == 1, [
        r.getMessage() for r in warn_records if r.levelno >= logging.WARNING
    ]


def test_size_caps_content_length_over_cap_fails_before_reading(
    body_server: Callable[..., _BodyServer],
) -> None:
    """A Content-Length over the cap fails without draining the body."""
    big = padded_envelope(2, "too-big", MAX_CONFIG_BODY_BYTES + 1)
    srv = body_server(lambda n: big, True)
    tr = _transport(srv.url)
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        r = tr._fetch_leg(0, abort=20.0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert r.error is not None
    # big itself is pre-allocated; the server only copies small slices.
    assert peak < 2 * MiB, f"peak {peak / MiB:.1f} MiB: body was read"


@pytest.mark.parametrize("content_length", [True, False], ids=["content-length", "chunked"])
def test_size_caps_10_5_control_12mib_body_installs(
    body_server: Callable[..., _BodyServer], content_length: bool
) -> None:
    env = padded_envelope(2, "twelve-mib", 12 * MiB)
    srv = body_server(lambda n: env, content_length)
    tr = _transport(srv.url)
    r = tr._fetch_leg(0, abort=20.0)
    assert r.error is None and r.envelope is not None
    assert tr._etags == {srv.url: '"etag-1"'}
    assert tr.fetch() is not None
