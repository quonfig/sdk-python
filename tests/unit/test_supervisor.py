"""Supervisor contract Test 8: auth-class response (qfg-goi1.10.25).

Contract: integration-test-data ``chaos/supervisor-test-contract.md`` Test 8
(a)-(g) at v2026.10.16. Plan: project/plans/2026-10-07-sdk-auth-failure-rule.md
section 4 (rule R1-R6). Shape follows the sdk-go canary (sdk-go 12e0e6c,
``supervisor_test.go`` / ``supervisor_auth_rig_test.go``).

The rig follows the contract's fixture rule. The supervisor, the SSE client,
the fallback-poller schedule and the auth-failed state machine are all real.
Only these are faked:

- the SSE worker's server: a local HTTP server whose status per connect
  attempt the test scripts;
- the config fetch legs: one local HTTP server per leg, each answering from a
  per-leg script ``leg(leg_index, cycle) -> Resp`` (``cycle`` counts
  primary-leg requests, so cycle 1 is the init fetch);
- the clock: every background wait the SDK requests goes through the client's
  ``_test_wait_hook``, which records the requested duration and waits
  1/COMPRESSION of it. The tests assert the REQUESTED values (the contract's
  anti-vacuity rule) without sleeping minutes.

Everything lands in one ordered event log (waits, server hits with the
client's state at that moment, log records), so the assertions can cut it at
the entry ERROR and the exit INFO.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator, List, Optional

import pytest

from quonfig import Quonfig

KEY = "t8-secret-sdk-key"
COMPRESSION = 2000.0
AUTH_STATUSES = [401, 403, 404, 410]
PROBE_MIN, PROBE_MAX = 150.0, 300.0
SSE_BASE = 1.0  # sdk-python's first SSE reconnect backoff (sse.py)
SSE_CAP = 30.0  # sse.py _SSE_RECONNECT_MAX_DELAY_S, matching sdk-go/sdk-node (qfg-q8lb)
POLL_INTERVAL = 60.0  # fallback_poll_interval_ms default


@dataclass
class Resp:
    status: int
    gen: int = 0  # envelope generation for a 200
    delay: float = 0.0  # seconds to sleep before answering (timeout sub-tests)
    exit: bool = False  # mark the rig "exited" when served


@dataclass
class Event:
    kind: str  # "wait:<kind>", "hit:sse", "hit:0", "hit:1", "log"
    d: float = 0.0
    level: int = 0
    msg: str = ""
    state: str = ""
    active: bool = False


@dataclass
class Rig:
    sse_status: int
    sse_after: int = 0  # SSE status once an exit response was served (0 = same)
    leg: Callable[[int, int], Resp] = lambda leg, cycle: Resp(status=200, gen=1)
    events: List[Event] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    leg0_calls: int = 0
    exited: bool = False
    client: Optional[Quonfig] = None
    sse_gate: threading.Event = field(default_factory=threading.Event)
    servers: List[ThreadingHTTPServer] = field(default_factory=list)

    def record(self, e: Event) -> None:
        with self.lock:
            self.events.append(e)

    def snapshot(self) -> List[Event]:
        with self.lock:
            return list(self.events)

    def wait(self, kind: str, seconds: float) -> float:
        self.record(Event(kind="wait:" + kind, d=seconds))
        return seconds / COMPRESSION

    def client_state(self) -> tuple[str, bool]:
        c = self.client
        if c is None:
            return "", False
        return c.connection_state(), c.fallback_poller_active()

    # -- servers --------------------------------------------------------------

    def _serve(self, handle: Callable[[BaseHTTPRequestHandler], None]) -> str:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                try:
                    handle(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args: Any) -> None:
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    @staticmethod
    def _reply(h: BaseHTTPRequestHandler, status: int, body: bytes = b"") -> None:
        h.send_response(status)
        if body:
            h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Connection", "close")
        h.end_headers()
        if body:
            h.wfile.write(body)

    def _sse_handler(self, h: BaseHTTPRequestHandler) -> None:
        # Hold SSE until the init fetch has finished, so cycle 1 is always
        # the init fetch (sdk-python starts SSE alongside it).
        self.sse_gate.wait(5)
        st, active = self.client_state()
        self.record(Event(kind="hit:sse", state=st, active=active))
        status = self.sse_after if (self.exited and self.sse_after) else self.sse_status
        self._reply(h, status)

    def _leg_handler(self, leg: int) -> Callable[[BaseHTTPRequestHandler], None]:
        def handle(h: BaseHTTPRequestHandler) -> None:
            with self.lock:
                if leg == 0:
                    self.leg0_calls += 1
                cycle = self.leg0_calls
            st, active = self.client_state()
            self.record(Event(kind=f"hit:{leg}", state=st, active=active))
            resp = self.leg(leg, cycle)
            if resp.delay:
                time.sleep(resp.delay)
            if resp.exit:
                self.exited = True
            if resp.status == 200:
                body = (
                    '{"configs":[],"meta":{"version":"gen-%d","environment":"Production",'
                    '"generation":%d}}' % (resp.gen, resp.gen)
                ).encode()
                self._reply(h, 200, body)
            else:
                self._reply(h, resp.status)

        return handle

    def start(self, legs: int = 2, **overrides: Any) -> Quonfig:
        sse_url = self._serve(self._sse_handler)
        urls = [self._serve(self._leg_handler(i)) for i in range(legs)]
        kwargs: dict = dict(
            sdk_key=KEY,
            api_urls=urls,
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            init_timeout_ms=8000,
        )
        kwargs.update(overrides)
        c = Quonfig(**kwargs)
        c._test_wait_hook = self.wait
        assert c._transport is not None
        c._transport._Transport__test_stream_url_override = sse_url  # type: ignore[attr-defined]
        self.client = c
        c.init()
        c._initialized.wait(8)
        self.sse_gate.set()
        return c

    def stop(self) -> None:
        self.sse_gate.set()
        if self.client is not None:
            self.client.close()
        for s in self.servers:
            s.shutdown()
            s.server_close()

    def await_(self, what: str, cond: Callable[[List[Event]], bool]) -> List[Event]:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            ev = self.snapshot()
            if cond(ev):
                return ev
            time.sleep(0.002)
        ev = self.snapshot()
        pytest.fail(f"timed out waiting for {what}; events:\n{dump(ev)}")


class _LogCapture(logging.Handler):
    def __init__(self, rig: Rig) -> None:
        super().__init__(level=logging.DEBUG)
        self.rig = rig

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        self.rig.record(Event(kind="log", level=record.levelno, msg=msg))


@pytest.fixture
def make_rig() -> Iterator[Callable[..., Rig]]:
    rigs: List[Rig] = []
    qlog = logging.getLogger("quonfig")
    old_level = qlog.level

    def make(**kw: Any) -> Rig:
        r = Rig(**kw)
        h = _LogCapture(r)
        qlog.addHandler(h)
        rigs.append(r)
        handlers.append(h)
        return r

    handlers: List[logging.Handler] = []
    qlog.setLevel(logging.DEBUG)
    yield make
    for r in rigs:
        r.stop()
    for h in handlers:
        qlog.removeHandler(h)
    qlog.setLevel(old_level)


# -- event-log helpers ---------------------------------------------------------


def count(ev: List[Event], kind: str) -> int:
    return sum(1 for e in ev if e.kind == kind)


def index(ev: List[Event], start: int, kind: str, nth: int = 1) -> int:
    """Index of the nth (1-based) event of ``kind`` at or after ``start``, or -1."""
    for i in range(max(start, 0), len(ev)):
        if ev[i].kind == kind:
            nth -= 1
            if nth == 0:
                return i
    return -1


def log_index(ev: List[Event], start: int, level: int) -> int:
    for i in range(max(start, 0), len(ev)):
        if ev[i].kind == "log" and ev[i].level == level:
            return i
    return -1


def logs(ev: List[Event], level: int) -> List[str]:
    return [e.msg for e in ev if e.kind == "log" and e.level == level]


def waits(ev: List[Event], kind: str) -> List[float]:
    return [e.d for e in ev if e.kind == "wait:" + kind]


def check_sse_ladder(waits_: List[float], label: str) -> None:
    """sdk-python's SSE reconnect ladder from a fresh stream: first sleep is
    the 1s base, each next one is the previous x2 with multiplicative jitter
    [0.8, 1.2], capped at 60s."""
    prev = None
    for k, w in enumerate(waits_):
        if prev is None:
            assert w == pytest.approx(SSE_BASE), f"{label}: SSE sleep #1 = {w}, want {SSE_BASE}"
        else:
            lo, hi = min(prev * 1.6, SSE_CAP), min(prev * 2.4, SSE_CAP)
            assert lo - 1e-9 <= w <= hi + 1e-9, (
                f"{label}: SSE sleep #{k + 1} = {w}, want within [{lo}, {hi}] (normal ladder)"
            )
        prev = w


def dump(ev: List[Event]) -> str:
    out = []
    for i, e in enumerate(ev):
        if e.kind == "log":
            out.append(f"{i:4d} log {logging.getLevelName(e.level)} {e.msg}")
        elif e.kind.startswith("wait:"):
            out.append(f"{i:4d} {e.kind} {e.d}")
        else:
            out.append(f"{i:4d} {e.kind} state={e.state} active={e.active}")
    return "\n".join(out)


def entering_cycle(ev: List[Event]) -> int:
    """Index of the entering cycle's primary hit: the first primary-leg request
    after SSE started (cycle 1 is the init fetch, which finishes before SSE)."""
    first_sse = index(ev, 0, "hit:sse")
    assert first_sse >= 0, "SSE never attempted"
    i = index(ev, first_sse, "hit:0")
    assert i >= 0, f"no fetch cycle after SSE started; events:\n{dump(ev)}"
    return i


def mentions_status(msg: str, status: int) -> bool:
    return re.search(rf"\b{status}\b", msg) is not None


# -----------------------------------------------------------------------------
# Test 8 (a)-(c)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("status", AUTH_STATUSES)
def test_supervisor_auth_class_enters_auth_failed_and_probes_slowly(
    make_rig: Callable[..., Rig], status: int
) -> None:
    """SSE gets auth-class, then the fallback poller's cycle gets auth-class on
    every leg -> auth-failed: 6 probes each requested at a wait in [150s,
    300s], SSE and the poller off, disconnected, one ERROR, no WARN."""
    r = make_rig(
        sse_status=status,
        leg=lambda leg, cycle: Resp(200, gen=1) if cycle == 1 else Resp(status),
    )
    r.start(2)
    # Entering cycle (2) + 6 probes (3..8), then the 7th probe wait.
    ev = r.await_("6 probes", lambda ev: r.leg0_calls >= 8 and count(ev, "wait:probe") >= 7)

    enter = entering_cycle(ev)
    pre = ev[:enter]
    # (a) before the entering cycle: SSE on its normal ladder, no fetch cycle
    # other than the init fetch. sdk-python engages the poller at once when
    # SSE never connected, so the poller's immediate fetch is the entering
    # cycle.
    assert count(pre, "hit:sse") >= 1
    check_sse_ladder(waits(pre, "sse"), "pre-entry")
    assert count(ev[index(ev, 0, "hit:sse") : enter], "hit:1") == 0

    err = log_index(ev, enter, logging.ERROR)
    assert err >= 0, f"no ERROR logged after the entering cycle; events:\n{dump(ev)}"
    post = ev[err:]

    # (a) the next 6 probes: requested at waits in [150s, 300s], no doubling.
    probes = waits(post, "probe")
    assert len(probes) >= 7, probes
    for i, w in enumerate(probes[:7]):
        assert PROBE_MIN <= w <= PROBE_MAX, f"probe wait #{i + 1} = {w}"

    # (b) while auth-failed: no SSE attempt, the poller never engages or
    # ticks, each probe is one cycle across both legs, disconnected throughout.
    last = index(ev, err, "wait:probe", 7)
    window = ev[err:last]
    for k in ("hit:sse", "wait:sse", "wait:engage", "wait:poll"):
        assert count(window, k) == 0, f"{count(window, k)} {k} events while auth-failed"
    for p in range(1, 7):
        a = index(ev, err, "wait:probe", p)
        b = index(ev, err, "wait:probe", p + 1)
        seg = ev[a:b]
        assert (count(seg, "hit:0"), count(seg, "hit:1")) == (1, 1), (
            f"probe {p} asked primary {count(seg, 'hit:0')} and secondary "
            f"{count(seg, 'hit:1')} times, want 1 and 1"
        )
    for e in window:
        if e.kind in ("hit:0", "hit:1"):
            assert (e.state, e.active) == ("disconnected", False), (
                f"during a probe: connection_state={e.state!r} poller_active={e.active}"
            )

    # (c) from entry through the 6 probes: one ERROR naming the status (never
    # the key), no other ERROR, no WARN.
    counted = ev[enter:last]
    errs = logs(counted, logging.ERROR)
    assert len(errs) == 1 and mentions_status(errs[0], status), errs
    assert logs(counted, logging.WARNING) == []
    for e in ev:
        if e.kind == "log":
            assert KEY not in e.msg, f"log line contains the SDK key: {e.msg}"


# -----------------------------------------------------------------------------
# Test 8 (d)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("status", AUTH_STATUSES)
@pytest.mark.parametrize("case", ["secondary-200", "primary-304", "secondary-304"])
def test_supervisor_auth_failed_exits_on_first_valid_answer(
    make_rig: Callable[..., Rig], status: int, case: str
) -> None:
    """A probe that gets a valid 200 on the secondary leg only, or a 304 on
    any leg, exits: install, one INFO, SSE restarts from its base backoff."""
    primary, second, want_gen = {
        "secondary-200": (Resp(status), Resp(200, gen=2, exit=True), 2),
        "primary-304": (Resp(304, exit=True), Resp(status), 1),
        "secondary-304": (Resp(status), Resp(304, exit=True), 1),
    }[case]

    def leg(i: int, cycle: int) -> Resp:
        # cycle 2 enters, cycle 3 is a rejected probe, cycle 4 exits.
        if cycle == 1:
            return Resp(200, gen=1)
        if cycle == 4:
            return primary if i == 0 else second
        if cycle > 4:
            return Resp(503)  # back on the normal regime
        return Resp(status)

    r = make_rig(sse_status=status, sse_after=503, leg=leg)
    c = r.start(2)

    def done(ev: List[Event]) -> bool:
        i = index(ev, 0, "wait:probe", 2)
        if not r.exited or i < 0:
            return False
        h = index(ev, i, "hit:sse")
        return h >= 0 and index(ev, h, "wait:sse") >= 0

    ev = r.await_("exit and an SSE restart", done)
    assert c.held_generation() == want_gen

    enter = entering_cycle(ev)
    err = log_index(ev, enter, logging.ERROR)
    exit_ = log_index(ev, err, logging.INFO) if err >= 0 else -1
    assert err >= 0 and exit_ >= 0, f"want an entry ERROR then an exit INFO:\n{dump(ev)}"
    restart = index(ev, exit_, "hit:sse")
    assert count(ev[err:exit_], "hit:sse") == 0
    assert count(ev[err:exit_], "wait:probe") == 2, "want 2 probe waits (cycles 3 and 4)"
    assert len(logs(ev[enter:restart], logging.INFO)) == 1, logs(ev[enter:restart], logging.INFO)
    assert len(logs(ev[enter:restart], logging.ERROR)) == 1
    assert "leaving auth-failed" in ev[exit_].msg
    # SSE restarts from its normal base: the next ordinary SSE failure
    # requests the first rung of the ladder.
    after_exit = waits(ev[restart:], "sse")
    assert after_exit, "no SSE sleep after the restart"
    check_sse_ladder(after_exit[:1], "post-exit")
    assert count(ev[exit_:], "wait:probe") == 0


# -----------------------------------------------------------------------------
# Test 8 (e)-(g): normal regime
# -----------------------------------------------------------------------------


def check_normal_regime(r: Rig) -> None:
    """The run never entered auth-failed: no ERROR, no probe, the SSE ladder
    continues across the poller's cycles, the poller keeps its interval."""
    ev = r.await_("3 poller cycles", lambda ev: r.leg0_calls >= 4)
    ev = r.snapshot()
    assert logs(ev, logging.ERROR) == []
    assert count(ev, "wait:probe") == 0, "never auth-failed: want no probe"
    check_sse_ladder(waits(ev, "sse"), "whole run")
    first_poll = entering_cycle(ev)
    r.await_("SSE after the poller's cycle", lambda ev: count(ev[first_poll:], "hit:sse") > 0)
    polls = waits(r.snapshot(), "poll")
    assert polls and all(w == POLL_INTERVAL for w in polls), polls
    for e in ev[first_poll:]:
        if e.kind == "hit:0":
            assert e.state == "falling_back", f"connection_state during a poll = {e.state!r}"


@pytest.mark.parametrize("status", AUTH_STATUSES)
@pytest.mark.parametrize("which", ["secondary-200", "primary-200"])
def test_supervisor_transient_primary_rejection_stays_normal(
    make_rig: Callable[..., Rig], status: int, which: str
) -> None:
    """(e) SSE auth-class, then poller cycles with a 200 on a leg: no
    auth-failed, no ERROR, SSE and the poller stay on their normal schedule."""

    def leg(i: int, cycle: int) -> Resp:
        if cycle == 1 or which == "primary-200" or i == 1:
            return Resp(200, gen=1)
        return Resp(status)

    r = make_rig(sse_status=status, leg=leg)
    r.start(2)
    check_normal_regime(r)


@pytest.mark.parametrize("status", AUTH_STATUSES)
@pytest.mark.parametrize("second", ["503", "timeout"])
def test_supervisor_mixed_legs_primary_auth_enters(
    make_rig: Callable[..., Rig], status: int, second: str
) -> None:
    """(f) primary auth-class + secondary 503 or timeout enters auth-failed."""

    def leg(i: int, cycle: int) -> Resp:
        if cycle == 1:
            return Resp(200, gen=1)
        if i == 0:
            return Resp(status)
        if second == "timeout":
            return Resp(200, gen=9, delay=0.4)
        return Resp(503)

    r = make_rig(sse_status=status, leg=leg)
    r.start(2, config_fetch_hedge_abort_ms=100)
    ev = r.await_("a probe after entry", lambda ev: count(ev, "wait:probe") >= 2)
    errs = logs(ev, logging.ERROR)
    assert len(errs) == 1 and mentions_status(errs[0], status), errs
    for i, w in enumerate(waits(ev, "probe")):
        assert PROBE_MIN <= w <= PROBE_MAX, f"probe wait #{i + 1} = {w}"


@pytest.mark.parametrize("status", AUTH_STATUSES)
def test_supervisor_mixed_legs_secondary_auth_does_not_enter(
    make_rig: Callable[..., Rig], status: int
) -> None:
    """(f) primary 503 + secondary auth-class does not enter."""

    def leg(i: int, cycle: int) -> Resp:
        if cycle == 1:
            return Resp(200, gen=1)
        return Resp(503) if i == 0 else Resp(status)

    r = make_rig(sse_status=status, leg=leg)
    r.start(2)
    check_normal_regime(r)


def test_supervisor_non_auth_status_stays_on_ladder(make_rig: Callable[..., Rig]) -> None:
    """(g) a 503 on every leg (and on SSE) stays on the normal ladder and never
    enters auth-failed."""
    r = make_rig(
        sse_status=503,
        leg=lambda i, cycle: Resp(200, gen=1) if cycle == 1 else Resp(503),
    )
    r.start(2)
    check_normal_regime(r)


# -----------------------------------------------------------------------------
# Polling mode (R2/R3): the first rejected poll enters; the probe wait is
# max(T, poll interval). A single-leg client's only leg is the primary.
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("status", AUTH_STATUSES)
@pytest.mark.parametrize("interval_s", [60.0, 400.0])
def test_supervisor_auth_failed_polling_mode_waits_max_of_period_and_interval(
    make_rig: Callable[..., Rig], status: int, interval_s: float
) -> None:
    exit_cycle = 6

    def leg(i: int, cycle: int) -> Resp:
        if cycle == 1:
            return Resp(200, gen=1)
        if cycle >= exit_cycle:
            return Resp(200, gen=2, exit=True)
        return Resp(status)

    r = make_rig(sse_status=503, leg=leg)
    c = r.start(
        1,
        enable_sse=False,
        fallback_poll_interval_ms=int(interval_s * 1000),
    )
    ev = r.await_(
        "exit, then a normal poll wait",
        lambda ev: r.exited and count(ev[index(ev, 0, "hit:0", exit_cycle) :], "wait:poll") >= 1,
    )
    assert count(ev, "hit:sse") == 0
    errs = logs(ev, logging.ERROR)
    assert len(errs) == 1 and mentions_status(errs[0], status), errs

    err = log_index(ev, 0, logging.ERROR)
    exit_hit = index(ev, 0, "hit:0", exit_cycle)
    probes = waits(ev[err:exit_hit], "probe")
    assert probes, f"no probe waits; events:\n{dump(ev)}"
    lo, hi = (interval_s, interval_s) if interval_s > PROBE_MAX else (PROBE_MIN, PROBE_MAX)
    for i, w in enumerate(probes):
        assert lo <= w <= hi, f"probe wait #{i + 1} = {w}, want within [{lo}, {hi}]"
    assert count(ev[err:exit_hit], "wait:poll") == 0
    for e in ev[err : exit_hit + 1]:
        if e.kind == "hit:0":
            assert (e.state, e.active) == ("disconnected", False), (e.state, e.active)
    # Exit: one INFO, then polling resumes at the interval (no immediate
    # fetch: the first wait after the exiting cycle is a poll wait).
    after = ev[exit_hit:]
    assert any("leaving auth-failed" in m for m in logs(after, logging.INFO))
    first_wait = next(e for e in after if e.kind.startswith("wait:"))
    assert (first_wait.kind, first_wait.d) == ("wait:poll", interval_s)
    assert c.connection_state() == "connected"


# =============================================================================
# Test 9: first-frame rule against a local server (qfg-goi1.10.44)
# =============================================================================
#
# Contract: integration-test-data ``chaos/supervisor-test-contract.md`` "Rule:
# a connection is `connected` at its first valid frame" + Test 9 (a)-(d) +
# Test 2 "Reset", at v2026.10.16.4. Plan:
# project/plans/2026-10-12-sse-connected-first-frame.md. Shape follows the
# sdk-go canary (sdk-go 2891ee2, ``sse_first_frame_test.go``).
#
# Unlike Test 8 this drives the REAL SSE worker against a local 127.0.0.1
# server that writes chunked ``text/event-stream`` bodies, the way
# api-delivery does. Only the fallback poll interval is shrunk (option), and
# requested waits are recorded but NOT compressed.
#
# sdk-python keeps the bounded transparent reconnect (contract Rule, Jeff
# 2026-10-16): ``connected`` is held across a clean-EOF reconnect with no
# edge, until a connection ends with no frame.

T9_KEY = "t9.value"
SSE_INITIAL = 0.5  # sse.py _CLEAN_EOF_RECONNECT_DELAY_S: the reconnect delay after a frame


def t9_envelope(gen: int, value: str) -> bytes:
    rule = {"criteria": [{"operator": "ALWAYS_TRUE"}], "value": {"type": "string", "value": value}}
    return json.dumps(
        {
            "configs": [
                {
                    "id": "id-t9",
                    "key": T9_KEY,
                    "type": "config",
                    "valueType": "string",
                    "sendToClientSdk": False,
                    "default": {"rules": [rule]},
                    "environments": [{"id": "Production", "rules": [rule]}],
                }
            ],
            "meta": {"version": f"gen-{gen}", "environment": "Production", "generation": gen},
        }
    ).encode()


def sse_open(h: BaseHTTPRequestHandler) -> None:
    """200 + event-stream headers, chunked, flushed, no body byte yet."""
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Transfer-Encoding", "chunked")
    h.end_headers()
    h.wfile.flush()


def sse_write(h: BaseHTTPRequestHandler, data: bytes) -> None:
    h.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
    h.wfile.flush()


def sse_end(h: BaseHTTPRequestHandler) -> None:
    """Clean EOF: the terminating chunk, then the connection closes."""
    h.wfile.write(b"0\r\n\r\n")
    h.wfile.flush()
    h.close_connection = True


@dataclass
class T9:
    """Real client + local servers + recorders for one Test 9 sub-test."""

    body: Callable[["T9", BaseHTTPRequestHandler, int], None]
    rig: Rig = field(default_factory=lambda: Rig(sse_status=200))
    lock: threading.Lock = field(default_factory=threading.Lock)
    edges: List[tuple] = field(default_factory=list)  # (state, monotonic)
    sse_waits: List[float] = field(default_factory=list)
    attempts: int = 0
    config_hits: int = 0
    hold: threading.Event = field(default_factory=threading.Event)
    gate: threading.Event = field(default_factory=threading.Event)
    sampling: threading.Event = field(default_factory=threading.Event)
    first_connected_at: Optional[float] = None
    ever_connected: bool = False
    ever_not_connected_after_connected: bool = False
    ever_active: bool = False
    client: Optional[Quonfig] = None

    def _sse(self, h: BaseHTTPRequestHandler) -> None:
        self.gate.wait(5)  # cycle 1 is always the init fetch
        with self.lock:
            self.attempts += 1
            n = self.attempts
        self.body(self, h, n)

    def _config(self, h: BaseHTTPRequestHandler) -> None:
        with self.lock:
            self.config_hits += 1
            n = self.config_hits
        body = t9_envelope(1, "v1") if n == 1 else t9_envelope(2, "v2")
        Rig._reply(h, 200, body)

    def _wait(self, kind: str, seconds: float) -> float:
        if kind == "sse":
            with self.lock:
                self.sse_waits.append(seconds)
        return seconds

    def _on_state(self, state: str) -> None:
        with self.lock:
            self.edges.append((state, time.monotonic()))

    def _sample(self) -> None:
        c = self.client
        while not self.sampling.is_set() and c is not None:
            st = c.connection_state()
            active = c.fallback_poller_active()
            now = time.monotonic()
            with self.lock:
                if st == "connected":
                    if self.first_connected_at is None:
                        self.first_connected_at = now
                    self.ever_connected = True
                elif self.ever_connected:
                    self.ever_not_connected_after_connected = True
                if active:
                    self.ever_active = True
            time.sleep(0.001)

    def start(self, poll_interval_s: float) -> Quonfig:
        sse_url = self.rig._serve(self._sse)
        cfg_url = self.rig._serve(self._config)
        c = Quonfig(
            sdk_key=KEY,
            api_urls=[cfg_url],
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            init_timeout_ms=8000,
            fallback_poll_interval_ms=int(poll_interval_s * 1000),
            on_sse_connection_state_change=self._on_state,
        )
        c._test_wait_hook = self._wait
        assert c._transport is not None
        c._transport._Transport__test_stream_url_override = sse_url  # type: ignore[attr-defined]
        self.client = c
        threading.Thread(target=self._sample, daemon=True).start()
        c.init()
        assert c._initialized.wait(8)
        assert c.get(T9_KEY) == "v1"
        self.gate.set()
        return c

    def stop(self) -> None:
        self.sampling.set()
        self.hold.set()
        self.gate.set()
        if self.client is not None:
            self.client.close()
        for s in self.rig.servers:
            s.shutdown()
            s.server_close()

    def connected_edges(self) -> List[float]:
        with self.lock:
            return [t for s, t in self.edges if s == "connected"]

    def states(self) -> List[str]:
        with self.lock:
            return [s for s, _ in self.edges]


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


def wait_for(cond: Callable[[], bool], within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def test_first_frame_9a_headers_only_is_a_failed_attempt(t9: Callable[..., T9]) -> None:
    """9a: every attempt is 200 + headers, flush, clean close. No frame ever,
    so no `connected`, the backoff grows, and the poller delivers v2."""

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        sse_end(h)

    x = t9(body)
    c = x.start(poll_interval_s=0.5)
    t0 = time.monotonic()
    got_v2 = wait_for(lambda: c.fallback_poller_active() and c.get(T9_KEY) == "v2", 3.0)
    assert got_v2, (
        f"poller never delivered v2 (config frozen at {c.get(T9_KEY)!r}); "
        f"state={c.connection_state()} edges={x.states()} sse_waits={x.sse_waits}"
    )
    assert c.connection_state() == "falling_back"
    time.sleep(max(0.0, 3.0 - (time.monotonic() - t0)))
    assert x.connected_edges() == [], f"connected edge on a frameless stream: {x.states()}"
    assert not x.ever_connected, "connection_state() reported connected with no frame"
    assert x.attempts <= 4, f"{x.attempts} attempts in 3s: reconnect storm"
    with x.lock:
        w = list(x.sse_waits)
    assert w, "no reconnect sleep requested"
    check_sse_ladder(w, "9a")


def test_first_frame_9b_headers_early_frame_late(t9: Callable[..., T9]) -> None:
    """9b: headers flushed, keepalive 1s later, stream held. `connected` is
    reported at the keepalive, not at the headers."""
    sent: dict = {}

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        time.sleep(1.0)
        sent.setdefault("at", time.monotonic())
        sse_write(h, b": keepalive\n\n")
        x.hold.wait(10)

    x = t9(body)
    x.start(poll_interval_s=5.0)
    assert wait_for(lambda: "at" in sent and len(x.connected_edges()) >= 1, 3.0), x.states()
    time.sleep(0.2)
    edges = x.connected_edges()
    assert len(edges) == 1, x.states()
    assert edges[0] >= sent["at"] - 0.05, (
        f"connected edge {sent['at'] - edges[0]:.3f}s before the keepalive was sent"
    )
    assert x.first_connected_at is not None
    assert x.first_connected_at >= sent["at"] - 0.05, (
        f"connection_state() connected {sent['at'] - x.first_connected_at:.3f}s "
        "before the keepalive was sent"
    )
    assert x.attempts == 1


def test_first_frame_keepalive_on_a_close_delimited_stream_is_seen_at_once(
    t9: Callable[..., T9],
) -> None:
    """A non-chunked (close-delimited) stream: the lone 13-byte keepalive must
    be read when it lands. ``iter_content(128)`` would block on it until 128
    bytes or EOF, leaving the stream unconnected for many keepalive intervals."""
    sent: dict = {}

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Connection", "close")
        h.end_headers()
        sent.setdefault("at", time.monotonic())
        h.wfile.write(b": keepalive\n\n")
        h.wfile.flush()
        x.hold.wait(10)

    x = t9(body)
    x.start(poll_interval_s=5.0)
    assert wait_for(lambda: len(x.connected_edges()) >= 1, 2.0), x.states()
    assert x.connected_edges()[0] - sent["at"] < 0.5


def test_first_frame_9c_keepalive_then_close_holds_connected(t9: Callable[..., T9]) -> None:
    """9c: every attempt sends one keepalive then closes. Each connection
    delivered a frame, so python holds `connected` across the reconnects
    (bounded transparent reconnect): one edge, no error, initial delay."""

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        sse_write(h, b": keepalive\n\n")
        sse_end(h)

    x = t9(body)
    c = x.start(poll_interval_s=0.5)  # engage grace = 1s > any sleep at the initial delay
    time.sleep(2.5)
    assert x.attempts >= 3, x.attempts
    assert len(x.connected_edges()) == 1, x.states()
    assert "error" not in x.states(), x.states()
    assert not x.ever_not_connected_after_connected, "connected was not held across reconnects"
    with x.lock:
        w = list(x.sse_waits)
    assert w and all(s <= SSE_INITIAL + 1e-9 for s in w), w
    assert not x.ever_active, "poller engaged against a live stream"
    assert c.connection_state() == "connected"
    assert c.get(T9_KEY) == "v1"


def test_first_frame_9c_hold_is_bounded_by_the_next_connection(t9: Callable[..., T9]) -> None:
    """Bounded transparent reconnect: after a keepalive-then-close session the
    next connection delivers no frame. The hold ends there: `error` at once,
    the backoff grows, and the poller takes over (v2)."""

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        if n == 1:
            sse_write(h, b": keepalive\n\n")
        sse_end(h)

    x = t9(body)
    c = x.start(poll_interval_s=0.5)  # grace 1s
    assert wait_for(lambda: c.get(T9_KEY) == "v2", 5.0), (
        f"poller never took over; edges={x.states()} sse_waits={x.sse_waits}"
    )
    states = x.states()
    assert states.count("connected") == 1, states
    i = states.index("connected")
    assert "error" in states[i:], states
    with x.lock:
        w = list(x.sse_waits)
    # sleep 1: after the frame session (initial delay); sleep 2: after the
    # frameless one, the error ladder's 1s base, not the initial delay.
    assert len(w) >= 2, w
    assert w[0] <= SSE_INITIAL + 1e-9, w
    assert w[1] == pytest.approx(SSE_BASE), w


def test_first_frame_9d_envelope_control(t9: Callable[..., T9]) -> None:
    """9d: 200 + snapshot envelope, held open: one edge, connected, v2, no poller."""

    def body(x: T9, h: BaseHTTPRequestHandler, n: int) -> None:
        sse_open(h)
        sse_write(h, b"data: " + t9_envelope(2, "v2") + b"\n\n")
        x.hold.wait(10)

    x = t9(body)
    c = x.start(poll_interval_s=5.0)
    assert wait_for(lambda: c.get(T9_KEY) == "v2", 3.0), x.states()
    time.sleep(0.2)
    assert len(x.connected_edges()) == 1, x.states()
    assert c.connection_state() == "connected"
    assert not x.ever_active
    assert x.config_hits == 1


def test_first_frame_test2_reset_only_on_a_frame() -> None:
    """Test 2 Reset at the SSE-client level: a session that delivered a frame
    resets the backoff; a 200 with no frame does not."""
    from quonfig.sse import SSEClient
    from quonfig.store import ConfigStore
    from quonfig.transport import Transport

    rig = Rig(sse_status=200)
    lock = threading.Lock()
    hits = {"n": 0}
    hold = threading.Event()
    sleeps: List[float] = []
    done = threading.Event()

    def handle(h: BaseHTTPRequestHandler) -> None:
        with lock:
            hits["n"] += 1
            n = hits["n"]
        if n in (1, 3):
            Rig._reply(h, 503)
        elif n == 2:
            sse_open(h)
            sse_write(h, b"data: " + t9_envelope(2, "v2") + b"\n\n")
            sse_end(h)
        elif n == 4:
            sse_open(h)
            sse_end(h)
        else:
            done.set()
            sse_open(h)
            hold.wait(10)

    url = rig._serve(handle)
    shutdown = threading.Event()

    def wait_hook(kind: str, s: float) -> float:
        with lock:
            sleeps.append(s)
        return 0.001

    sse = SSEClient(
        Transport([url], "sk"),
        ConfigStore(),
        shutdown,
        install=lambda env: True,
        wait_hook=wait_hook,
    )
    sse._stream_url_override = url + "/api/v2/sse/config"
    sse.start()
    try:
        assert done.wait(10), f"never reached conn 5; sleeps={sleeps}"
        with lock:
            s = list(sleeps)
        assert len(s) == 4, s
        assert s[0] == pytest.approx(SSE_BASE), s  # conn1 503
        assert s[1] <= SSE_INITIAL + 1e-9, s  # conn2 frame + EOF: reset
        assert s[2] == pytest.approx(SSE_BASE), s  # conn3 503: ladder restarted by the frame
        assert s[3] >= SSE_BASE * 1.6 - 1e-9, s  # conn4 frameless: grown, NOT reset
    finally:
        hold.set()
        sse.stop()
        sse.join(2)
        for srv in rig.servers:
            srv.shutdown()
            srv.server_close()


def _sse_reset(h: BaseHTTPRequestHandler) -> None:
    """Abort the connection with a TCP RST (SO_LINGER 0), mid chunked body."""
    import socket
    import struct

    h.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    h.connection.close()
    h.close_connection = True


@pytest.mark.parametrize(
    "frame",
    [b": keepalive\n\n", b"data: " + t9_envelope(2, "v2") + b"\n\n"],
    ids=["keepalive", "envelope"],
)
def test_first_frame_test2_reset_after_a_frame_then_io_error(frame: bytes) -> None:
    """Test 2 Reset (contract supervisor-test-contract.md:205-208): a worker
    run that delivered a frame resets the backoff even when the connection
    then dies with an IO error (here a TCP reset mid chunked body), not only
    on a clean EOF. Every connection does frame-then-reset, so every requested
    reconnect sleep must be the 1s base; before the fix it ratcheted
    1 -> 2 -> 4 -> ... -> 30s (sdk-go sse_client.go:239-246)."""
    from quonfig.sse import SSEClient
    from quonfig.store import ConfigStore
    from quonfig.transport import Transport

    rig = Rig(sse_status=200)
    lock = threading.Lock()
    sleeps: List[float] = []
    done = threading.Event()

    def handle(h: BaseHTTPRequestHandler) -> None:
        sse_open(h)
        sse_write(h, frame)
        time.sleep(0.05)  # let the client read the frame before the reset
        _sse_reset(h)

    url = rig._serve(handle)
    shutdown = threading.Event()

    def wait_hook(kind: str, s: float) -> float:
        with lock:
            sleeps.append(s)
            if len(sleeps) >= 6:
                done.set()
        return 0.001

    sse = SSEClient(
        Transport([url], "sk"),
        ConfigStore(),
        shutdown,
        install=lambda env: True,
        wait_hook=wait_hook,
    )
    sse._stream_url_override = url + "/api/v2/sse/config"
    sse.start()
    try:
        assert done.wait(15), f"never reached 6 reconnects; sleeps={sleeps}"
        with lock:
            s = list(sleeps)
        assert all(x == pytest.approx(SSE_BASE) for x in s), s
    finally:
        sse.stop()
        sse.join(2)
        for srv in rig.servers:
            srv.shutdown()
            srv.server_close()


class _NullTransport:
    pass


@pytest.mark.parametrize(
    "name,chunks,frame",
    [
        ("empty body", [], False),
        ("keepalive", [b": keepalive\n\n"], True),
        ("bare colon comment", [b":\n\n"], True),
        ("keepalive cut off by EOF", [b": keepalive\n"], False),
        ("keepalive split across chunks", [b": keep", b"alive\n", b"\n"], True),
        ("crlf keepalive", [b": keepalive\r\n\r\n"], True),
        ("envelope", [b"data: " + t9_envelope(2, "v2") + b"\n\n"], True),
        ("envelope without blank line", [b"data: " + t9_envelope(2, "v2") + b"\n"], False),
        ("malformed data", [b"data: {not json\n\n"], False),
        ("json that is not an envelope", [b'data: {"error":"nope"}\n\n'], False),
        ("id-only block", [b"id: 7\n\n"], False),
        ("bare blank line", [b"\n"], False),
        ("comment plus field is not comment-only", [b": hi\nid: 7\n\n"], False),
        ("garbage block then keepalive", [b"data: {bad\n\n", b": keepalive\n\n"], True),
    ],
)
def test_first_frame_parser_table(name: str, chunks: List[bytes], frame: bool) -> None:
    """The frame table: frame seen, and exactly one `connected` edge, iff a
    valid frame arrived (contract Rule; canary shape item 2)."""
    from quonfig.sse import SSEClient
    from quonfig.store import ConfigStore

    states: List[str] = []
    sse = SSEClient(
        _NullTransport(),  # type: ignore[arg-type]
        ConfigStore(),
        threading.Event(),
        state_listener=states.append,
        install=lambda env: True,
    )
    seen = sse._parse_stream(iter(chunks), announce=True)
    assert seen is frame, name
    assert states.count("connected") == (1 if frame else 0), (name, states)
