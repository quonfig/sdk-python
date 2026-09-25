"""Telemetry transport contract T1-T8 (qfg-y8je.7), sdk-python implementation of
integration-test-data/chaos/telemetry-transport-contract.md. sdk-node
(test/telemetry-transport.test.ts) is the reference.

Fixture (tests/unit/telemetry_transport_fixtures.py): a real ``Quonfig``
client in datadir mode with the real reporter, queue and ``requests`` POST,
pointed at a scriptable stdlib HTTP stub; an injected manual clock drives the
ticks, the 30s floor, ``Retry-After`` and retained-batch age; ``caplog``
captures every level.

``requests`` has no timer the manual clock can drive, so the request timeout
runs on real time, compressed to ``FAST_TIMEOUT_MS`` (the contract allows
this), and T1 asserts the shipped 15000 / 5000 defaults separately. T8 runs
``close()`` on real time with the shipped 5s deadline.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

from quonfig import Quonfig
from quonfig.telemetry.transport_queue import (
    DROP_WARN_INTERVAL_MS,
    RESEND_FLOOR_MS,
    RETRY_AFTER_CAP_MS,
    SHUTDOWN_FLUSH_DEADLINE_MS,
    TELEMETRY_DEFAULTS,
)

from .telemetry_transport_fixtures import (
    MIN,
    Harness,
    ManualClock,
    TelemetryStub,
    log_count,
    warn_messages,
    write_datadir,
)

FAST_TIMEOUT_MS = 300


@pytest.fixture
def stub() -> Iterator[TelemetryStub]:
    s = TelemetryStub()
    yield s
    s.close()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def make(
    tmp_path: Path, stub: TelemetryStub, clock: ManualClock, monkeypatch: Any, caplog: Any
) -> Iterator[Any]:
    monkeypatch.delenv("QUONFIG_BACKEND_SDK_KEY", raising=False)
    monkeypatch.delenv("QUONFIG_DOMAIN", raising=False)
    monkeypatch.setattr(Quonfig, "_telemetry_clock", clock, raising=False)
    datadir = write_datadir(tmp_path)
    clients: list = []

    def _make(**overrides: Any) -> Harness:
        opts: dict = {"telemetry_timeout_ms": FAST_TIMEOUT_MS}
        opts.update(overrides)
        q = Quonfig(
            sdk_key="qf_sk_development_0000_dead",
            datadir=datadir,
            environment="Production",
            telemetry_url=stub.url,
            **opts,
        )
        q.init()
        clients.append(q)
        caplog.set_level(logging.DEBUG, logger="quonfig")
        caplog.clear()
        return Harness(q, clock)

    yield _make
    for q in clients:
        q.close()


# ---------------------------------------------------------------------------
# T1 - Timeout aborts and retains (P1, P5, P7)
# ---------------------------------------------------------------------------


def test_t1_timeout_aborts_and_retains(make: Any, stub: TelemetryStub, caplog: Any) -> None:
    h = make()
    h.record("A")
    stub.script({"hang": True}, {"status": 200})

    h.advance(MIN)  # tick 1: POST 0 hangs and is abandoned at the (compressed) timeout
    h.advance(15_000)
    assert stub.post_count == 1
    assert h.retained_count == 1
    assert log_count(caplog, "WARN") == 0
    assert log_count(caplog, "ERROR") == 0
    assert log_count(caplog, "DEBUG", r"Telemetry POST failed \(timeout\)") == 1

    h.advance(45_000)  # tick 2
    assert stub.post_count == 2
    assert stub.sha(1) == stub.sha(0)
    assert h.retained_count == 0
    assert log_count(caplog, "INFO", "recover") == 1
    assert log_count(caplog, "WARN") == 0


def test_t1_defaults_timeout_15000_connect_5000_interval_60000(make: Any) -> None:
    h = make(telemetry_timeout_ms=None)
    assert h.r.config.timeout_ms == 15_000
    assert h.r.config.connect_timeout_ms == 5_000
    assert h.r.config.flush_interval_ms == 60_000
    assert TELEMETRY_DEFAULTS.timeout_ms == 15_000
    assert TELEMETRY_DEFAULTS.connect_timeout_ms == 5_000


# ---------------------------------------------------------------------------
# T2 - 5xx retains verbatim and resends (P4, P5)
# ---------------------------------------------------------------------------


def test_t2_5xx_retains_verbatim_and_resends(make: Any, stub: TelemetryStub) -> None:
    h = make()
    h.record("A", 0)
    stub.script({"status": 503}, {"status": 503}, {"status": 200}, {"status": 200})

    h.advance(MIN)
    assert stub.post_count == 1
    assert h.retained_count == 1

    h.record("B", 3)
    h.advance(MIN)
    assert stub.post_count == 2
    assert h.retained_count == 2

    h.advance(MIN)
    assert stub.post_count == 4
    assert stub.sha(1) == stub.sha(0)
    assert stub.sha(2) == stub.sha(0)
    assert stub.has(3, "B")
    assert b'"key": "cfg-03"' in stub.body(3)
    assert not stub.has(3, "A")
    assert b'"key": "cfg-00"' not in stub.body(3)
    assert h.retained_count == 0


# ---------------------------------------------------------------------------
# T3 - Non-retryable 4xx (P3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 404])
def test_t3a_auth_status_disables_telemetry(
    make: Any, stub: TelemetryStub, caplog: Any, status: int
) -> None:
    h = make()
    h.record("A")
    stub.script({"status": 503}, {"status": status})
    h.advance(MIN)
    assert h.retained_count == 1

    h.record("B", 3)
    h.advance(MIN)
    assert stub.post_count == 2
    assert log_count(caplog, "ERROR", str(status)) == 1
    assert h.enabled is False
    assert h.retained_count == 0
    assert log_count(caplog, "WARN") == 0

    for k in range(3):
        h.record(f"C{k}", 6)
        h.advance(MIN)
    h.q.flush()
    assert stub.post_count == 2
    assert log_count(caplog, "ERROR") == 1


@pytest.mark.parametrize("status", [400, 413, 422])
def test_t3b_other_4xx_drops_the_batch_and_keeps_ticking(
    make: Any, stub: TelemetryStub, caplog: Any, status: int
) -> None:
    h = make()
    h.record("A")
    stub.script({"status": status, "body": "bad payload"}, {"status": 200})
    h.advance(MIN)
    assert stub.post_count == 1
    assert h.retained_count == 0
    assert log_count(caplog, "ERROR") == 1
    assert log_count(caplog, "ERROR", "bad payload") == 1
    assert log_count(caplog, "WARN") == 0
    assert h.enabled is True

    h.record("B", 3)
    h.advance(MIN)
    assert stub.post_count == 2
    assert stub.sha(1) != stub.sha(0)


def test_t3_408_is_retryable_anti_vacuity(make: Any, stub: TelemetryStub, caplog: Any) -> None:
    h = make()
    h.record("A")
    stub.script({"status": 408})
    h.advance(MIN)
    assert h.retained_count == 1
    assert h.enabled is True
    assert log_count(caplog, "ERROR") == 0


# ---------------------------------------------------------------------------
# T4 - Retry-After and the 30s floor (P4)
# ---------------------------------------------------------------------------


def test_t4a_30s_floor_after_a_failure(make: Any, stub: TelemetryStub) -> None:
    h = make(telemetry_flush_interval_ms=8_000)
    h.record("A")
    stub.script({"status": 503}, {"status": 200})
    h.advance(8_000)  # F = 8s
    assert stub.post_count == 1
    for _ in range(3):  # F+8, F+16, F+24
        h.advance(8_000)
        assert stub.post_count == 1
    h.advance(8_000)  # F+32: first tick at or after F+30
    assert stub.post_count == 2
    assert stub.sha(1) == stub.sha(0)
    assert RESEND_FLOOR_MS == 30_000


def test_t4b_retry_after_delta_seconds_honored(make: Any, stub: TelemetryStub) -> None:
    h = make()
    h.record("A")
    stub.script({"status": 429, "retry_after": "120"}, {"status": 200})
    h.advance(MIN)  # F = 60s
    h.advance(MIN)  # F+60
    h.advance(59_000)  # F+119
    assert stub.post_count == 1
    h.advance(1_000)  # F+120
    h.advance(MIN)  # the timer tick at 180s
    assert stub.post_count == 2
    assert stub.sha(1) == stub.sha(0)


def test_t4c_retry_after_clamped_to_600s_aged_batch_discarded_with_one_warn(
    make: Any, stub: TelemetryStub, caplog: Any
) -> None:
    h = make()
    h.record("A", 0)
    stub.script({"status": 503, "retry_after": "3600"}, {"status": 200})
    h.advance(MIN)  # F = 60s
    h.record("B", 3)
    for _ in range(9):
        h.advance(MIN)  # up to F+540
    h.advance(59_000)  # F+599
    assert stub.post_count == 1
    h.advance(1_000)  # F+600 (tick 11 is at 660s)
    h.advance(MIN)
    assert stub.post_count == 2
    assert stub.has(1, "B")
    assert not stub.has(1, "A")
    assert log_count(caplog, "WARN") == 1
    assert log_count(caplog, "WARN", "older than 5 min") == 1
    assert RETRY_AFTER_CAP_MS == 600_000


def test_t4d_retry_after_http_date_honored(
    make: Any, stub: TelemetryStub, clock: ManualClock
) -> None:
    from email.utils import formatdate

    h = make()
    h.record("A")
    at = formatdate((clock.now_ms() + 3 * MIN) / 1000, usegmt=True)
    stub.script({"status": 503, "retry_after": at})
    h.advance(MIN)  # F = 60s; Retry-After = 180s wall clock -> 120s after F
    h.advance(MIN)  # F+60
    assert stub.post_count == 1
    h.advance(MIN)  # F+120
    assert stub.post_count == 2
    assert stub.sha(1) == stub.sha(0)


# ---------------------------------------------------------------------------
# T5 - Caps under outage (P5, P6)
# ---------------------------------------------------------------------------


def test_t5_queue_caps_5_batches_oldest_evicted_resent_oldest_first(
    make: Any, stub: TelemetryStub
) -> None:
    h = make()
    stub.set_default({"status": 503})
    first_post: dict = {}
    for k in range(1, 9):
        h.record(f"E{k}", k)
        before = stub.post_count
        h.advance(MIN)
        assert stub.post_count == before + 1
        i = stub.post_count - 1
        for s in range(1, k + 1):
            if stub.has(i, f"E{s}") and f"E{s}" not in first_post:
                first_post[f"E{s}"] = stub.sha(i)
        assert h.retained_count <= 5
        assert h.retained_bytes <= h.r.config.max_retained_bytes
    assert h.retained_count == 5

    stub.set_default({"status": 200})
    start = stub.post_count
    h.advance(MIN)
    assert stub.post_count == start + 5
    for j, tag in enumerate(["E4", "E5", "E6", "E7", "E8"]):
        assert stub.has(start + j, tag)
        if tag in first_post:
            assert stub.sha(start + j) == first_post[tag]
    for i in range(start, start + 5):
        assert not stub.has(i, "E1")
    assert h.retained_count == 0


def test_t5_max_age_discards_batches_older_than_5_min(make: Any, stub: TelemetryStub) -> None:
    h = make()
    stub.set_default({"status": 503})
    for k in range(1, 4):
        h.record(f"E{k}", k)
        h.advance(MIN)
    h.advance(6 * MIN)
    assert h.retained_count == 0

    stub.set_default({"status": 200})
    before = stub.post_count
    h.advance(MIN)
    for i in range(before, stub.post_count):
        for tag in ("E1", "E2", "E3"):
            assert not stub.has(i, tag)


def test_t5_oversize_batch_is_dropped_not_retained(
    make: Any, stub: TelemetryStub, caplog: Any
) -> None:
    h = make(telemetry_max_retained_bytes=4096)
    for k in range(20):
        h.record(f"X{k}", k)
    stub.script({"status": 503})
    h.advance(MIN)
    assert stub.post_count == 1
    assert len(stub.body(0)) > 4096
    assert h.retained_count == 0
    assert h.retained_bytes == 0
    assert log_count(caplog, "WARN") == 1
    assert log_count(caplog, "WARN", "byte cap") == 1


def test_t5_shipped_queue_defaults(make: Any) -> None:
    h = make()
    assert h.r.config.max_retained_batches == 5
    assert h.r.config.max_retained_bytes == 2_097_152
    assert h.r.config.max_retained_age_ms == 300_000


def test_t5_aggregator_defaults_10000_each(make: Any) -> None:
    h = make()
    assert h.r.config.max_evaluation_summaries == 10_000
    assert h.r.config.max_context_shape_fields == 10_000
    assert h.r.config.max_example_contexts == 10_000
    assert h.r._eval_collector._max_data_size == 10_000
    assert h.r._ctx_collector._max_data_size == 10_000
    assert h.r._example_collector._max_data_size == 10_000


def test_t5_aggregator_cap_evaluation_summaries_existing_key_keeps_counting(
    make: Any, stub: TelemetryStub
) -> None:
    h = make(telemetry_max_evaluation_summaries=3)
    for i in range(6):
        h.q.get(f"cfg-0{i}", contexts={"user": {"key": "u"}})
    h.q.get("cfg-00", contexts={"user": {"key": "u"}})
    h.advance(MIN)
    ev = next(e for e in stub.json(0)["events"] if "summaries" in e)["summaries"]
    assert sorted(s["key"] for s in ev["summaries"]) == ["cfg-00", "cfg-01", "cfg-02"]
    c0 = next(s for s in ev["summaries"] if s["key"] == "cfg-00")
    assert c0["counters"][0]["count"] == 2


def test_t5_aggregator_cap_context_shape_fields(make: Any, stub: TelemetryStub) -> None:
    h = make(telemetry_max_context_shape_fields=3)
    h.q.get("cfg-00", contexts={"user": {"key": "u", "a": 1, "b": 2}, "team": {"c": 3, "d": 4}})
    h.q.get("cfg-00", contexts={"user": {"key": "u", "a": 1, "b": 2}, "team": {"c": 3, "d": 4}})
    h.advance(MIN)
    shapes = next(e for e in stub.json(0)["events"] if "contextShapes" in e)["contextShapes"]
    fields = [f for s in shapes["shapes"] for f in s["fieldTypes"]]
    assert len(fields) == 3


def test_t5_aggregator_cap_example_contexts(make: Any, stub: TelemetryStub) -> None:
    h = make(telemetry_max_example_contexts=3)
    for i in range(6):
        h.q.get("cfg-00", contexts={"user": {"key": f"u{i}"}})
    h.advance(MIN)
    ex = next(e for e in stub.json(0)["events"] if "exampleContexts" in e)["exampleContexts"]
    assert len(ex["examples"]) == 3


# ---------------------------------------------------------------------------
# T6 - Logging episodes (P7)
# ---------------------------------------------------------------------------


def test_t6a_blip_no_warn_one_recovery_info(make: Any, stub: TelemetryStub, caplog: Any) -> None:
    h = make()
    h.record("A")
    stub.script({"status": 503}, {"status": 200})
    h.advance(MIN)
    h.advance(MIN)
    assert stub.post_count == 2
    assert log_count(caplog, "WARN") == 0
    assert log_count(caplog, "INFO", "recover") == 1
    assert log_count(caplog, "DEBUG") >= 1
    assert log_count(caplog, "ERROR") == 0


def test_t6b_sustained_503_one_warn_at_first_drop_info_on_recovery(
    make: Any, stub: TelemetryStub, caplog: Any
) -> None:
    h = make()
    stub.set_default({"status": 503})
    for k in range(1, 6):
        h.record(f"E{k}", k)
        h.advance(MIN)
    assert log_count(caplog, "WARN") == 0
    h.record("E6", 6)
    h.advance(MIN)  # tick 6 evicts E1: the first drop
    assert log_count(caplog, "WARN") == 1
    warn = warn_messages(caplog)[0]
    assert "last POST result: 503" in warn
    assert "retained queue 5/5 batches" in warn
    assert "1 batch(es) dropped so far" in warn
    for k in range(7, 10):
        h.record(f"E{k}", k)
        h.advance(MIN)
    assert log_count(caplog, "WARN") == 1
    stub.set_default({"status": 200})
    h.advance(MIN)
    assert log_count(caplog, "INFO", "recover") == 1
    assert log_count(caplog, "ERROR") == 0


def test_t6c_warn_summary_at_most_once_per_10_min(
    make: Any, stub: TelemetryStub, caplog: Any
) -> None:
    h = make()
    stub.set_default({"status": 503})
    # Tick 6 (360s) is the first drop; the next WARN is due at >= 960s (tick 16).
    for k in range(1, 16):
        h.record(f"E{k}", k)
        h.advance(MIN)
    assert log_count(caplog, "WARN") == 1
    h.record("E16", 16)
    h.advance(MIN)
    assert log_count(caplog, "WARN") == 2
    assert (
        log_count(
            caplog, "WARN", r"still dropping data: \d+ batch\(es\) dropped in the last 10 min"
        )
        == 1
    )
    for k in range(17, 26):
        h.record(f"E{k}", k)
        h.advance(MIN)
    assert log_count(caplog, "WARN") == 2
    assert log_count(caplog, "ERROR") == 0
    assert DROP_WARN_INTERVAL_MS == 600_000


# ---------------------------------------------------------------------------
# T7 - One POST in flight (P2)
# ---------------------------------------------------------------------------


def test_t7_one_post_in_flight_skipped_windows_aggregate(make: Any, stub: TelemetryStub) -> None:
    h = make(telemetry_timeout_ms=10_000)
    h.record("A", 0)
    stub.script({"hang": True})
    first = threading.Thread(target=h.r.tick, daemon=True)
    first.start()
    stub.wait_for_posts(1)
    assert h.r.debug_state()["in_flight"] is True

    h.record("B", 3)
    h.r.tick()
    h.record("C", 6)
    h.r.tick()
    assert stub.post_count == 1

    stub.release(0, {"status": 200})
    first.join(timeout=5)
    assert not first.is_alive()
    h.r.tick()
    assert stub.post_count == 2
    assert stub.has(1, "B")
    assert stub.has(1, "C")
    assert not stub.has(1, "A")


# ---------------------------------------------------------------------------
# T8 - Shutdown (P8)
# ---------------------------------------------------------------------------


def test_t8_close_final_flush_5s_retained_not_drained_nothing_left_running(
    make: Any, stub: TelemetryStub
) -> None:
    # Shipped timeout (15s), so the close() bound is the real 5s deadline.
    h = make(telemetry_timeout_ms=None)
    stub.script({"status": 503}, {"status": 503})
    h.record("A", 0)
    h.advance(MIN)
    h.record("B", 3)
    h.advance(MIN)
    assert stub.post_count == 2
    assert h.retained_count == 2
    h.record("C", 6)
    stub.set_default({"hang": True})

    started = time.monotonic()
    h.q.close()
    elapsed = time.monotonic() - started
    assert SHUTDOWN_FLUSH_DEADLINE_MS == 5_000
    assert elapsed < 5.0 + 1.0, f"close() took {elapsed:.2f}s"

    stub.wait_for_posts(3)
    assert stub.post_count == 3
    assert stub.has(2, "C")
    assert not stub.has(2, "A")
    assert not stub.has(2, "B")
    assert stub.sha(2) not in (stub.sha(0), stub.sha(1))

    h.advance(10 * MIN)
    h.r.flush()
    assert stub.post_count == 3
    thread = h.r._thread
    assert thread is None or not thread.is_alive() or thread.daemon
    if thread is not None:
        thread.join(timeout=1)
        assert not thread.is_alive(), "the reporter thread outlived close()"
    h.q.close()  # second close is a no-op
    h.r.close()
    assert stub.post_count == 3


# ---------------------------------------------------------------------------
# P8 at interpreter exit: atexit final flush, bounded, fork-safe
# ---------------------------------------------------------------------------

_EXIT_SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    from quonfig import Quonfig

    q = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        datadir=sys.argv[1],
        environment="Production",
        telemetry_url=sys.argv[2],
    )
    q.init()
    q.get("cfg-00", contexts={"user": {"key": "exit-0"}})
    if sys.argv[3] == "fork":
        # Anything holding the parent's reporter keeps it alive in the child
        # after the at-fork hook drops ``q._telemetry``.
        inherited = q._telemetry
        pid = os.fork()
        if pid == 0:
            sys.exit(0)  # a normal exit: runs atexit in the child
        os.waitpid(pid, 0)
    # No close(): the atexit hook owns the final flush.
    """
)


def _run_exit_script(tmp_path: Path, url: str, mode: str) -> float:
    datadir = write_datadir(tmp_path)
    script = tmp_path / "exit_script.py"
    script.write_text(_EXIT_SCRIPT)
    env = dict(os.environ)
    env.pop("QUONFIG_BACKEND_SDK_KEY", None)
    env.pop("QUONFIG_DOMAIN", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    # macOS: keep a forked child off the fork-hostile proxy lookup (requests ->
    # _scproxy -> libobjc aborts the child), so a child that wrongly POSTs
    # really reaches the stub instead of crashing silently.
    env["NO_PROXY"] = "127.0.0.1"
    env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(script), datadir, url, mode],
        env=env,
        capture_output=True,
        timeout=30,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr.decode()
    return elapsed


def test_p8_atexit_flushes_the_live_window(tmp_path: Path, stub: TelemetryStub) -> None:
    _run_exit_script(tmp_path, stub.url, "plain")
    stub.wait_for_posts(1)
    assert stub.post_count == 1
    assert stub.has(0, "exit")


def test_p8_atexit_never_blocks_exit_past_the_deadline(tmp_path: Path, stub: TelemetryStub) -> None:
    stub.set_default({"hang": True})
    elapsed = _run_exit_script(tmp_path, stub.url, "plain")
    assert stub.post_count == 1
    # 5s deadline plus interpreter start-up and import slack.
    assert elapsed < 5.0 + 4.0, f"interpreter exit took {elapsed:.2f}s"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_p8_atexit_in_a_forked_child_does_not_resend_the_parents_window(
    tmp_path: Path, stub: TelemetryStub
) -> None:
    _run_exit_script(tmp_path, stub.url, "fork")
    stub.wait_for_posts(1)
    time.sleep(0.2)
    assert stub.post_count == 1, "the forked child re-posted the parent's window at exit"


# ---------------------------------------------------------------------------
# Units: status classes and Retry-After parsing (P3, P4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,cls",
    [
        (200, "ok"),
        (204, "ok"),
        (301, "rejected"),
        (400, "rejected"),
        (401, "auth"),
        (403, "auth"),
        (404, "auth"),
        (408, "retryable"),
        (413, "rejected"),
        (422, "rejected"),
        (429, "retryable"),
        (500, "retryable"),
        (503, "retryable"),
    ],
)
def test_classify_status(status: int, cls: str) -> None:
    from quonfig.telemetry.transport_queue import classify_status

    assert classify_status(status) == cls


def test_parse_retry_after_ms() -> None:
    from email.utils import formatdate

    from quonfig.telemetry.transport_queue import parse_retry_after_ms

    now = 1_790_294_400_000.0
    assert parse_retry_after_ms(None, now) is None
    assert parse_retry_after_ms("", now) is None
    assert parse_retry_after_ms("120", now) == 120_000
    assert parse_retry_after_ms(" 5 ", now) == 5_000
    assert parse_retry_after_ms("3600", now) == 600_000
    assert parse_retry_after_ms(formatdate((now + 90_000) / 1000, usegmt=True), now) == 90_000
    assert parse_retry_after_ms(formatdate((now - 90_000) / 1000, usegmt=True), now) == 0
    assert parse_retry_after_ms("soon", now) is None
