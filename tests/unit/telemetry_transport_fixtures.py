"""Fixture for the telemetry transport contract tests (qfg-y8je.7).

Implements the fixture of integration-test-data/chaos/telemetry-transport-contract.md
for sdk-python:

* ``TelemetryStub``: a real stdlib ``ThreadingHTTPServer`` on 127.0.0.1:0,
  scriptable per received POST (status, optional ``Retry-After``, or hang
  until released). Records the raw body of every POST, including ones the
  client later abandons.
* ``ManualClock``: the injected telemetry clock (the contract's ``advance``).
  It never fires the reporter's background timer; ``Harness.advance`` runs the
  due ticks itself, synchronously, on the test thread.
* ``Harness``: a real ``Quonfig`` client (datadir mode, real reporter, real
  queue, real ``requests`` POST) wired to the stub and the clock.

Only the endpoint, the clock and the logger (pytest ``caplog``) are mocked.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Union

StubStep = Dict[str, Any]  # {"status": 503, "retry_after": "120", "body": "..."} or {"hang": True}


class TelemetryStub:
    def __init__(self) -> None:
        outer = self
        self._lock = threading.Lock()
        self._bodies: List[bytes] = []
        self._script: List[StubStep] = []
        self._default: StubStep = {"status": 200}
        self._held: Dict[int, "tuple[threading.Event, List[StubStep]]"] = {}
        self._closing = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                with outer._lock:
                    i = len(outer._bodies)
                    outer._bodies.append(body)
                    step = outer._script.pop(0) if outer._script else outer._default
                    if step.get("hang"):
                        released = threading.Event()
                        slot: List[StubStep] = []
                        outer._held[i] = (released, slot)
                if step.get("hang"):
                    while not released.wait(0.05):
                        if outer._closing.is_set():
                            return
                    with outer._lock:
                        outer._held.pop(i, None)
                    if not slot:
                        return
                    step = slot[0]
                try:
                    self.send_response(int(step["status"]))
                    if step.get("retry_after") is not None:
                        self.send_header("Retry-After", str(step["retry_after"]))
                    payload = str(step.get("body", "{}")).encode()
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass  # the client gave up on a held request

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def script(self, *steps: StubStep) -> None:
        with self._lock:
            self._script.extend(steps)

    def set_default(self, step: StubStep) -> None:
        with self._lock:
            self._default = step

    @property
    def post_count(self) -> int:
        with self._lock:
            return len(self._bodies)

    def body(self, i: int) -> bytes:
        with self._lock:
            return self._bodies[i]

    def sha(self, i: int) -> str:
        return hashlib.sha256(self.body(i)).hexdigest()

    def json(self, i: int) -> Any:
        return json.loads(self.body(i))

    def has(self, i: int, tag: str) -> bool:
        """POST i carries evaluation set ``tag`` (its example-context key)."""
        return f'"{tag}-0"'.encode() in self.body(i)

    def wait_for_posts(self, n: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while self.post_count < n:
            if time.monotonic() > deadline:
                raise AssertionError(f"stub saw {self.post_count} POSTs, want {n}")
            time.sleep(0.005)

    def release(self, i: int, step: StubStep) -> None:
        with self._lock:
            released, slot = self._held[i]
            slot.append(step)
        released.set()

    def close(self) -> None:
        self._closing.set()
        self._server.shutdown()
        self._server.server_close()


class ManualClock:
    """Injected telemetry clock. ``now_ms`` only moves when the test says so."""

    def __init__(self, start_ms: float = 1_790_294_400_000.0) -> None:  # 2026-09-25T00:00Z
        self.t = start_ms

    def now_ms(self) -> float:
        return self.t

    def wait(self, event: threading.Event, timeout_s: float) -> bool:
        # The reporter's background timer never fires under the manual clock:
        # tests run ticks through ``Harness.advance``. Block until close().
        return event.wait()


def write_datadir(tmp_path: Path, n: int = 20) -> str:
    """A workspace with string configs cfg-00..cfg-{n-1}, one ALWAYS_TRUE rule each."""
    datadir = tmp_path / "workspace"
    datadir.mkdir()
    (datadir / "quonfig.json").write_text(json.dumps({"environments": ["Production"]}))
    configs = datadir / "configs"
    configs.mkdir()
    for i in range(n):
        key = f"cfg-{i:02d}"
        rule = {
            "criteria": [{"operator": "ALWAYS_TRUE"}],
            "value": {"type": "string", "value": f"v-{key}"},
        }
        (configs / f"{key}.json").write_text(
            json.dumps(
                {
                    "id": f"id-{key}",
                    "key": key,
                    "type": "config",
                    "valueType": "string",
                    "sendToClientSdk": False,
                    "default": {"rules": [rule]},
                    "environments": [{"id": "Production", "rules": [rule]}],
                }
            )
        )
    return str(datadir)


MIN = 60_000


class Harness:
    """A real client on the stub + manual clock. ``advance`` is the contract's."""

    def __init__(self, client: Any, clock: ManualClock) -> None:
        self.q = client
        self.clock = clock
        self.r = client._telemetry
        assert self.r is not None, "telemetry reporter not constructed"
        self._start = clock.now_ms()
        self._ticks = 0

    def record(self, tag: str, cfg_base: int = 0) -> None:
        """Evaluation set ``tag``: three evaluations over configs
        cfg_base..cfg_base+2, each with a distinct context key ``{tag}-i``."""
        for i in range(3):
            key = f"cfg-{(cfg_base + i) % 20:02d}"
            self.q.get(key, contexts={"user": {"key": f"{tag}-{i}"}})

    def advance(self, ms: float) -> None:
        """Move the clock by ``ms``, running every tick that falls due (tick k
        at start + k * flush interval) synchronously, in time order."""
        target = self.clock.t + ms
        interval = self.r.config.flush_interval_ms
        while True:
            due = self._start + (self._ticks + 1) * interval
            if due > target:
                break
            self.clock.t = due
            self._ticks += 1
            self.r.tick()
        self.clock.t = target

    @property
    def retained_count(self) -> int:
        return int(self.r.debug_state()["retained_count"])

    @property
    def retained_bytes(self) -> int:
        return int(self.r.debug_state()["retained_bytes"])

    @property
    def enabled(self) -> bool:
        return bool(self.r.debug_state()["enabled"])


def log_count(caplog: Any, level: str, pattern: Union[str, None] = None) -> int:
    """The contract's ``log_count(level, /re/)`` over the SDK's telemetry logger."""
    import re

    want = {"DEBUG": "DEBUG", "INFO": "INFO", "WARN": "WARNING", "ERROR": "ERROR"}[level]
    rx = re.compile(pattern, re.IGNORECASE) if pattern else None
    n = 0
    for rec in caplog.records:
        if not rec.name.startswith("quonfig.telemetry"):
            continue
        if rec.levelname != want:
            continue
        if rx is not None and not rx.search(rec.getMessage()):
            continue
        n += 1
    return n


def warn_messages(caplog: Any) -> List[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("quonfig.telemetry") and r.levelname == "WARNING"
    ]


__all__ = [
    "MIN",
    "Harness",
    "ManualClock",
    "TelemetryStub",
    "log_count",
    "warn_messages",
    "write_datadir",
]
