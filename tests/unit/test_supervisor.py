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
