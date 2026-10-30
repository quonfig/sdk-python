"""Layer 2 restart supervisor (qfg-11ct).

The fallback poller thread runs inside a supervisor boundary: an unhandled
exception in the loop body logs at ERROR, counts one
``quonfig_sdk_worker_restart_total{layer="2"}`` (the ``worker_restart_layer2``
diagnostic), waits an exponential backoff (500ms doubling to a 30s cap) and
restarts the body, unless the poller was disengaged or the client closed.
Mirrors sdk-go ``supervisor.runWorker`` (Layer "2").
"""

from __future__ import annotations

import threading
import time
from typing import List, Tuple

from quonfig import Quonfig
from quonfig.store import ConfigStore
from quonfig.transport import CycleOutcome, FallbackPoller


def _wait_for(cond, timeout: float = 5.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class _Rig:
    def __init__(self, crash_polls: int) -> None:
        self.lock = threading.Lock()
        self.cycles = 0
        self.diag: List[str] = []
        self.waits: List[Tuple[str, float]] = []
        self._crash_polls = crash_polls
        self.shutdown = threading.Event()
        self.poller = FallbackPoller(
            transport=None,  # type: ignore[arg-type]
            store=ConfigStore(),
            interval_seconds=60.0,
            shutdown_event=self.shutdown,
            cycle=self._cycle,
            wait_hook=self._wait,
            on_diagnostic=self._record,
        )

    def _cycle(self) -> CycleOutcome:
        with self.lock:
            self.cycles += 1
        return CycleOutcome(answered=True)

    def _record(self, kind: str) -> None:
        with self.lock:
            self.diag.append(kind)

    def _wait(self, kind: str, seconds: float) -> float:
        with self.lock:
            self.waits.append((kind, seconds))
            if kind == "poll" and self._crash_polls > 0:
                self._crash_polls -= 1
                raise RuntimeError("boom: bug in the poll loop body")
        return 0.001

    def restarts(self) -> int:
        with self.lock:
            return self.diag.count("worker_restart_layer2")

    def restart_waits(self) -> List[float]:
        with self.lock:
            return [s for k, s in self.waits if k == "restart"]


def test_crash_in_loop_body_counts_a_restart_and_the_poller_resumes() -> None:
    rig = _Rig(crash_polls=1)
    rig.poller.engage("test")
    try:
        assert _wait_for(lambda: rig.restarts() == 1)
        # The respawned body fetches again: the poller resumed.
        assert _wait_for(lambda: rig.cycles >= 3)
        assert rig.poller.is_active() is True
        assert rig.restarts() == 1
        assert rig.restart_waits() == [0.5]
    finally:
        rig.poller.disengage("test")


def test_restart_backoff_doubles_from_500ms_and_caps_at_30s() -> None:
    rig = _Rig(crash_polls=9)
    rig.poller.engage("test")
    try:
        assert _wait_for(lambda: rig.restarts() == 9)
        assert rig.restart_waits() == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    finally:
        rig.poller.disengage("test")


def test_crash_after_disengage_is_not_a_restart() -> None:
    gate = threading.Event()
    rig = _Rig(crash_polls=0)
    original = rig._wait

    def wait(kind: str, seconds: float) -> float:
        if kind == "poll":
            gate.wait(5.0)
            raise RuntimeError("raised while winding down")
        return original(kind, seconds)

    rig.poller._wait_hook = wait
    rig.poller.engage("test")
    assert _wait_for(lambda: rig.cycles >= 1)
    thread = rig.poller._thread
    rig.poller.disengage("test")
    gate.set()
    assert thread is not None
    thread.join(2.0)
    assert not thread.is_alive()
    assert rig.restarts() == 0


def test_client_diagnostics_expose_worker_restart_layer2() -> None:
    client = Quonfig(
        sdk_key="sdk-test",
        api_urls=["http://localhost:0"],
        fallback_poll_enabled=True,
        fallback_poll_interval_ms=60000,
    )
    try:
        assert client._chaos_diagnostics()["worker_restart_layer2"] == 0
        client._record_diagnostic("worker_restart_layer2")
        d = client._chaos_diagnostics()
        assert d["worker_restart_layer2"] == 1
        assert d["worker_restart_layer1"] == 0
    finally:
        client.close()
