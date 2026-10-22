"""Cross-SDK chaos harness — sdk-python runner (qfg-47c2.8).

Drives scenarios in ``integration-test-data/chaos/scenarios/`` against the SDK
via toxiproxy. Mirrors ``sdk-node/chaos/run-chaos.test.ts`` and
``sdk-go/chaos_test.go`` so the same YAML, expression vocabulary, and
expectation polling apply per-language.

Run via ``scripts/run-chaos.sh`` (which boots toxiproxy + api-delivery first).

Environment knobs:
    TOXIPROXY_URL           admin API base       (default http://127.0.0.1:8474)
    CHAOS_SSE_PORT          chaos SSE port       (default 18550)
    CHAOS_HTTP_PORT         chaos HTTP port      (default 18551)
    CHAOS_API_DELIVERY_URL  upstream api-delivery URL (set by run-chaos.sh)
    CHAOS_UPSTREAM_HOST     toxiproxy upstream hostname (default host.docker.internal)
    CHAOS_ONLY              comma list of scenario numbers to run, e.g. "01,02"
    CHAOS_SKIP              comma list of scenario numbers to skip
    CHAOS_POLL_MS           expectation poll interval (default 250)

Excluded from default ``pytest`` collection via the ``testpaths = ["tests"]``
setting in ``pyproject.toml``; invoke explicitly with ``pytest chaos/``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pytest
import requests
import yaml

from chaos.timeline import ExpState, step, validate_expectations
from quonfig import Quonfig

# ----- paths -----

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_SCENARIO_DIR = _REPO_ROOT / "integration-test-data" / "chaos" / "scenarios"


# ----- env knobs -----


def _env(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val and len(val) > 0 else default


def _split_csv(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


TOXIPROXY_URL = _env("TOXIPROXY_URL", "http://127.0.0.1:8474")
CHAOS_SSE_PORT = int(_env("CHAOS_SSE_PORT", "18550"))
CHAOS_HTTP_PORT = int(_env("CHAOS_HTTP_PORT", "18551"))
CHAOS_POLL_MS = int(_env("CHAOS_POLL_MS", "250"))
CHAOS_ONLY = _split_csv(os.environ.get("CHAOS_ONLY"))
CHAOS_SKIP = _split_csv(os.environ.get("CHAOS_SKIP"))


# ----- toxiproxy admin client -----


class Toxiproxy:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self._sess = requests.Session()

    def ping(self) -> bool:
        try:
            r = self._sess.get(f"{self.base}/version", timeout=5)
            return r.ok
        except Exception:
            return False

    def upsert_proxy(self, name: str, listen: str, upstream: str) -> None:
        try:
            self._sess.delete(f"{self.base}/proxies/{name}", timeout=5)
        except Exception:
            pass
        r = self._sess.post(
            f"{self.base}/proxies",
            json={"name": name, "listen": listen, "upstream": upstream, "enabled": True},
            timeout=5,
        )
        if not r.ok:
            raise RuntimeError(f"upsertProxy {name}: {r.status_code} {r.text}")

    def clear_toxics(self, proxy: str) -> None:
        try:
            r = self._sess.get(f"{self.base}/proxies/{proxy}/toxics", timeout=5)
        except Exception:
            return
        if not r.ok:
            return
        for t in r.json():
            try:
                self._sess.delete(f"{self.base}/proxies/{proxy}/toxics/{t['name']}", timeout=5)
            except Exception:
                pass

    def set_enabled(self, proxy: str, enabled: bool) -> None:
        r = self._sess.post(f"{self.base}/proxies/{proxy}", json={"enabled": enabled}, timeout=5)
        if not r.ok:
            raise RuntimeError(f"setEnabled {proxy}: {r.status_code} {r.text}")

    def add_toxic(
        self, proxy: str, name: str, type_: str, stream: str, attributes: Dict[str, Any]
    ) -> None:
        r = self._sess.post(
            f"{self.base}/proxies/{proxy}/toxics",
            json={
                "name": name,
                "type": type_,
                "stream": stream or "downstream",
                "attributes": attributes,
            },
            timeout=5,
        )
        if not r.ok:
            raise RuntimeError(f"addToxic {proxy}/{name}: {r.status_code} {r.text}")

    def remove_toxic(self, proxy: str, name: str) -> None:
        try:
            self._sess.delete(f"{self.base}/proxies/{proxy}/toxics/{name}", timeout=5)
        except Exception:
            pass


# ----- chaos injection -----


def _apply_inject(tp: Toxiproxy, inj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = inj.get("name") or "anon"
    if "sse_silent_stall_after_ms" in inj:
        tp.add_toxic(
            "sse", name, "timeout", "downstream", {"timeout": inj["sse_silent_stall_after_ms"]}
        )
        return {"proxy": "sse", "toxic": name}
    if "sse_latency_ms" in inj:
        tp.add_toxic("sse", name, "latency", "downstream", {"latency": inj["sse_latency_ms"]})
        return {"proxy": "sse", "toxic": name}
    if "sse_bandwidth_kbps" in inj:
        tp.add_toxic("sse", name, "bandwidth", "downstream", {"rate": inj["sse_bandwidth_kbps"]})
        return {"proxy": "sse", "toxic": name}
    if "sse_down_ms" in inj:
        tp.set_enabled("sse", False)
        return {"enable": ["sse"]}
    if "both_down_ms" in inj:
        tp.set_enabled("sse", False)
        tp.set_enabled("http", False)
        return {"enable": ["sse", "http"]}
    if "sse_half_open_after_bytes" in inj:
        # Toxiproxy is TCP-only and can't truly model "server returns 200 then
        # closes after N bytes" — the limit_data toxic this used to call only
        # trips on the NEXT upstream byte, which for SSE is the 30s heartbeat,
        # outside the typical within_ms=15s window. The closest TCP-only
        # analog is to disable the proxy: existing SSE connections drop, new
        # attempts are refused. Leave it disabled until the matching `clear`
        # step so the SDK's reconnect attempts fail visibly (sdk-ruby's
        # ld-eventsource only fires on_error on ECONNREFUSED, not on clean
        # FIN). qfg-47c2.29.
        tp.set_enabled("sse", False)
        return {"enable": ["sse"]}
    if "sse_http_status" in inj:
        # toxiproxy is TCP-only and cannot script an HTTP status. Fail the run
        # rather than silently pass a scenario whose fault never landed
        # (qfg-6vno.5).
        raise ValueError(
            f"inject: sse_http_status={inj['sse_http_status']} is not supported by this rig "
            "(toxiproxy is TCP-only)"
        )
    if inj.get("proxy") and inj.get("toxic"):
        toxic = inj["toxic"]
        tp.add_toxic(
            inj["proxy"], name, str(toxic.get("type")), "downstream", toxic.get("attributes") or {}
        )
        return {"proxy": inj["proxy"], "toxic": name}
    raise ValueError(f"unknown inject shape {inj!r}: the chaos runner does not implement it")


def _clear_inject(tp: Toxiproxy, st: Optional[Dict[str, Any]]) -> None:
    if not st:
        return
    if st.get("toxic") and st.get("proxy"):
        tp.remove_toxic(st["proxy"], st["toxic"])
    for p in st.get("enable", []):
        tp.set_enabled(p, True)


def _apply_process(tp: Toxiproxy, p: Dict[str, Any]) -> None:
    if p.get("action") == "kill_sse_proxy":
        count = int(p.get("count") or 1)
        interval_ms = int(p.get("interval_ms") or 1000)
        for i in range(count):
            tp.set_enabled("sse", False)
            time.sleep(0.2)
            tp.set_enabled("sse", True)
            if i < count - 1:
                time.sleep(max(0.0, (interval_ms - 200) / 1000.0))
    else:
        raise ValueError(
            f"unknown process action {p.get('action')!r}: the chaos runner does not implement it"
        )


# ----- SDK probe -----


class _LogTap(logging.Handler):
    """Captures all `quonfig.*` log records into the probe's log buffer."""

    def __init__(self, probe: Any) -> None:
        super().__init__(level=logging.DEBUG)
        self._probe = probe

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._probe.log(record.levelname, record.getMessage())
        except Exception:
            pass


# Python level names -> the contract's sdkLog levels. sdkLog(level, re) counts
# lines at EXACTLY that level (chaos README), so 'warn' never matches ERROR.
_LOG_LEVELS = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "WARN": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}

# The connectionState() enum fixed by the supervisor contract; SDKs may not
# invent new strings, so neither may a scenario row (qfg-6vno.5).
CONNECTION_STATES = ("initializing", "connected", "disconnected", "falling_back")

INIT_STATES = ("pending", "returned", "raised")

# worker_restart_total{layer="2"}: sdk-python's Layer 2 fallback poller has no
# restart supervisor, so the SDK has no such counter. Reported SKIPPED rather
# than answered with a constant 0 (qfg-6vno.5).
LAYER2_RESTART_SKIP_REASON = (
    "sdk-python has no layer-2 worker restart counter: the fallback poller has no "
    "restart supervisor (qfg-6vno.5, qfg-11ct)"
)


class ChaosProbe:
    """Reads every asserted value from the SDK (qfg-6vno.5).

    Connection state, fallback state, liveness, the Layer 1 restart and SSE
    attempt counters and process liveness all come from the client's own
    surfaces (``connection_state()``, ``fallback_poller_active()``,
    ``last_successful_refresh()``, the test-only ``_chaos_diagnostics()``).
    The probe keeps only what the runner itself owns: the init call's outcome
    (``initState()``), the SDK's ``on_config_update`` callbacks after init
    returned (``readyEventCount()``) and the captured SDK log lines.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._client: Optional[Any] = None
        self.init_state = "pending"
        self.ready_events = 0
        self.logs: List[tuple[str, str]] = []
        self.log_lock = threading.Lock()

    def set_client(self, client: Any) -> None:
        with self.lock:
            self._client = client

    def _c(self) -> Optional[Any]:
        with self.lock:
            return self._client

    def set_init_state(self, state: str) -> None:
        with self.lock:
            self.init_state = state

    def on_config_update(self) -> None:
        """The SDK's ``on_config_update`` callback fired. Counted as a ready
        event only once the init call has returned (chaos README,
        ``client.readyEventCount()``)."""
        with self.lock:
            if self.init_state != "pending":
                self.ready_events += 1

    def connection_state(self) -> str:
        c = self._c()
        return c.connection_state() if c is not None else "initializing"

    def fallback_poller_active(self) -> bool:
        c = self._c()
        return bool(c is not None and c.fallback_poller_active())

    def diagnostics(self) -> Dict[str, Any]:
        c = self._c()
        if c is None:
            return {
                "sse_connect_attempts": 0,
                "worker_restart_layer1": 0,
                "sse_worker_alive": False,
            }
        return c._chaos_diagnostics()

    def process_still_alive(self) -> bool:
        """The SDK's SSE worker is still running: a supervisor that let a
        user-callback throw kill the worker fails this."""
        return bool(self.diagnostics().get("sse_worker_alive"))

    def ready(self) -> bool:
        c = self._c()
        return bool(c is not None and c.is_ready())

    def get_string(self, key: str, default: str) -> Any:
        c = self._c()
        return c.get_string(key, default) if c is not None else default

    def last_refresh_ms(self) -> int:
        """Epoch ms of ``client.last_successful_refresh()``, 0 before the first.

        Reads the SDK's liveness accessor, which a 304 / guard-dropped poll also
        advances; an install-only ``on_update`` stamp would freeze on a static
        rig config (qfg-goi1.7 probe rewire).
        """
        c = self._c()
        stamp = c.last_successful_refresh() if c is not None else None
        return int(stamp.timestamp() * 1000) if stamp is not None else 0

    def log(self, level: str, msg: str) -> None:
        canonical = _LOG_LEVELS.get(level.upper(), level.lower())
        with self.log_lock:
            self.logs.append((canonical, msg))

    def sdk_metric(self, name: str, labels: Dict[str, str]) -> tuple[float, str]:
        """Return ``(value, status)`` for an SDK-side metric, read from the
        SDK's ``_chaos_diagnostics()``.

        ``status`` is ``"known"``, ``"skip"`` (the SDK has no such counter;
        see ``LAYER2_RESTART_SKIP_REASON``) or ``"unknown"`` (a metric name
        the probe does not implement, so the evaluator fails loudly instead of
        comparing against a silent 0). Mirrors sdk-go's ``chaosProbe.sdkMetric``.
        """
        if name == "quonfig_sdk_worker_restart_total":
            if labels.get("layer") == "2":
                return 0.0, "skip"
            return float(self.diagnostics()["worker_restart_layer1"]), "known"
        if name == "quonfig_sse_connect_attempts_total":
            return float(self.diagnostics()["sse_connect_attempts"]), "known"
        return 0.0, "unknown"

    def log_matches(self, level: str, regex: re.Pattern) -> int:
        want = _LOG_LEVELS.get(level.upper(), level.lower())
        with self.log_lock:
            return sum(1 for lvl, msg in self.logs if lvl == want and regex.search(msg))


# ----- expression evaluator -----

_RE_CONN_STATE_EQ = re.compile(r"^client\.connectionState\(\)\s*(==|!=)\s*'([^']+)'$")
_RE_FALLBACK_EQ = re.compile(r"^client\.fallbackPollerActive\(\)\s*==\s*(true|false)$")
_RE_PROC_ALIVE_EQ = re.compile(r"^client\.processStillAlive\(\)\s*==\s*(true|false)$")
_RE_LAST_REFRESH = re.compile(
    r"^client\.lastSuccessfulRefresh\(\)\s*(>=|>|<=|<|==)\s*\(now\(\)\s*-\s*(\d+)\)$"
)
_RE_SDK_METRIC = re.compile(
    r"^client\.sdkMetric\(\s*'([^']+)'\s*(?:,\s*layer=\s*'([^']+)'\s*)?\)\s*"
    r"(>=|<=|==|!=|<|>)\s*(\d+)$"
)
_RE_SERVER_METRIC = re.compile(r"^server_metric\(\s*'([^']+)'\s*\)\s*(>=|<=|==|!=|<|>)\s*(\d+)$")
_RE_SDK_LOG = re.compile(
    r"^client\.sdkLog\(\s*'([^']+)'\s*,\s*/(.+)/(i?)\s*\)\s*(>=|<=|==|!=|<|>)\s*(\d+)$"
)
_RE_INIT_STATE = re.compile(r"^client\.initState\(\)\s*(==|!=)\s*'([^']+)'$")
_RE_READY = re.compile(r"^client\.ready\(\)\s*==\s*(true|false)$")
_RE_READY_EVENTS = re.compile(r"^client\.readyEventCount\(\)\s*(>=|<=|==|!=|<|>)\s*(\d+)$")
_RE_GET_STRING = re.compile(
    r"^client\.getString\(\s*'([^']+)'\s*,\s*'([^']*)'\s*\)\s*(==|!=)\s*'([^']*)'$"
)


def _split_outside_quotes_and_regex(expr: str, sep: str) -> List[str]:
    out: List[str] = []
    in_sq = False
    in_re = False
    start = 0
    i = 0
    while i < len(expr):
        c = expr[i]
        if c == "'" and not in_re:
            in_sq = not in_sq
        elif c == "/" and not in_sq:
            in_re = not in_re
        if not in_sq and not in_re and expr[i : i + len(sep)] == sep:
            out.append(expr[start:i])
            start = i + len(sep)
            i += len(sep)
            continue
        i += 1
    out.append(expr[start:])
    return out


def _compare(op: str, a: float, b: float) -> bool:
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    return False


# Why server_metric(...) is not evaluated by this rig (Decision 6 in
# project/sdk-quality-check/META-ANALYSIS.md): api-delivery exports its metrics
# only by OTLP push, so there is nothing to scrape from here. Server-side lag is
# covered by the staging drill (qfg-47c2.19) and the QuonfigSubscriberLagHigh
# alert. Mirrors sdk-ruby chaos/expressions.rb (qfg-goi1.1.3).
SERVER_METRIC_SKIP_REASON = (
    "server-side metric; api-delivery exports via OTLP only, no scrape endpoint "
    "in the rig; covered by staging drill qfg-47c2.19 and the "
    "QuonfigSubscriberLagHigh alert"
)


@dataclass
class EvalResult:
    """Outcome of evaluating one expectation expression.

    ``status`` is ``"pass"``, ``"fail"`` or ``"skip"``. ``skipped`` lists every
    skipped leaf with its reason so the run can print a tally; a skipped leaf is
    neutral inside a compound (see ``_evaluate``).
    """

    status: str
    reason: str = ""
    skipped: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "pass"


def _eval_leaf(expr: str, probe: ChaosProbe) -> EvalResult:
    expr = expr.strip()
    m = _RE_CONN_STATE_EQ.match(expr)
    if m:
        op, want = m.group(1), m.group(2)
        if want not in CONNECTION_STATES:
            return _leaf(
                False,
                f"{want!r} is not a connectionState() value "
                f"(supervisor contract: {', '.join(CONNECTION_STATES)})",
            )
        got = probe.connection_state()
        ok = (got == want) if op == "==" else (got != want)
        return _leaf(ok, f"connectionState={got} {op} {want}")
    m = _RE_FALLBACK_EQ.match(expr)
    if m:
        want = m.group(1) == "true"
        got = probe.fallback_poller_active()
        return _leaf(got == want, f"fallbackPollerActive={got} want {want}")
    m = _RE_PROC_ALIVE_EQ.match(expr)
    if m:
        want = m.group(1) == "true"
        alive = probe.process_still_alive()
        return _leaf(alive == want, f"processStillAlive={alive} want {want}")
    m = _RE_INIT_STATE.match(expr)
    if m:
        op, want = m.group(1), m.group(2)
        if want not in INIT_STATES:
            return _leaf(False, f"{want!r} is not an initState() value")
        with probe.lock:
            got = probe.init_state
        ok = (got == want) if op == "==" else (got != want)
        return _leaf(ok, f"initState={got} {op} {want}")
    m = _RE_READY.match(expr)
    if m:
        want = m.group(1) == "true"
        got = probe.ready()
        return _leaf(got == want, f"ready={got} want {want}")
    m = _RE_READY_EVENTS.match(expr)
    if m:
        op, want_n = m.group(1), float(m.group(2))
        with probe.lock:
            got_n = probe.ready_events
        return _leaf(_compare(op, got_n, want_n), f"readyEventCount={got_n} {op} {want_n}")
    m = _RE_GET_STRING.match(expr)
    if m:
        key, default, op, want = m.group(1), m.group(2), m.group(3), m.group(4)
        got = probe.get_string(key, default)
        ok = (got == want) if op == "==" else (got != want)
        return _leaf(ok, f"getString({key!r}, {default!r})={got!r} {op} {want!r}")
    m = _RE_LAST_REFRESH.match(expr)
    if m:
        op = m.group(1)
        ago = int(m.group(2))
        last = probe.last_refresh_ms()
        threshold = int(time.time() * 1000) - ago
        ok = _compare(op, last, threshold)
        return _leaf(ok, f"lastSuccessfulRefresh={last} {op} (now()-{ago})={threshold}")
    m = _RE_SDK_METRIC.match(expr)
    if m:
        metric, layer, op, want_str = m.group(1), m.group(2), m.group(3), m.group(4)
        labels = {"layer": layer} if layer else {}
        got, status = probe.sdk_metric(metric, labels)
        if status == "skip":
            why = f"sdkMetric({metric},layer={layer or ''}) {op} {want_str}: {LAYER2_RESTART_SKIP_REASON}"
            return EvalResult("skip", why, [why])
        if status != "known":
            return _leaf(
                False, f"unknown sdkMetric {metric!r}: the chaos probe does not implement it"
            )
        want = float(want_str)
        ok = _compare(op, got, want)
        return _leaf(ok, f"sdkMetric({metric},layer={layer or ''})={got} {op} {want}")
    m = _RE_SERVER_METRIC.match(expr)
    if m:
        # Explicit SKIP, never a silent 0 (qfg-goi1.1.4, Decision 6).
        why = f"server_metric({m.group(1)}) {m.group(2)} {m.group(3)}: {SERVER_METRIC_SKIP_REASON}"
        return EvalResult("skip", why, [why])
    m = _RE_SDK_LOG.match(expr)
    if m:
        level, pattern, flags, op, want_str = m.groups()
        regex = re.compile(pattern, re.I if flags else 0)
        got = probe.log_matches(level, regex)
        want = float(want_str)
        ok = _compare(op, got, want)
        return _leaf(ok, f"sdkLog({level},/{pattern}/{flags})={got} {op} {want}")
    return _leaf(False, f"unrecognized expression: {expr}")


def _leaf(ok: bool, reason: str) -> EvalResult:
    return EvalResult("pass" if ok else "fail", reason)


def _evaluate(expr: str, probe: ChaosProbe) -> EvalResult:
    """Evaluate ``expr`` against ``probe``.

    A skipped leaf is neutral in a compound: an AND passes when every
    non-skipped leaf passes, an OR passes only when a non-skipped leaf passes,
    and a compound whose leaves are all skipped is itself skipped.
    """
    expr = expr.strip()
    if not expr:
        return EvalResult("pass")
    # Every leaf is evaluated (no short-circuit) so the skipped tally is complete.
    if " OR " in expr:
        results = [_evaluate(p, probe) for p in _split_outside_quotes_and_regex(expr, " OR ")]
        skipped = [leaf for r in results for leaf in r.skipped]
        if any(r.status == "pass" for r in results):
            return EvalResult("pass", "", skipped)
        reasons = [r.reason for r in results if r.status == "fail"]
        if not reasons:
            return EvalResult("skip", "OR: all leaves skipped", skipped)
        return EvalResult("fail", "OR: " + " | ".join(reasons), skipped)
    if " AND " in expr:
        results = [_evaluate(p, probe) for p in _split_outside_quotes_and_regex(expr, " AND ")]
        skipped = [leaf for r in results for leaf in r.skipped]
        for r in results:
            if r.status == "fail":
                return EvalResult("fail", "AND: " + r.reason, skipped)
        if not any(r.status == "pass" for r in results):
            return EvalResult("skip", "AND: all leaves skipped", skipped)
        return EvalResult("pass", "", skipped)
    return _eval_leaf(expr, probe)


# ----- runner -----


_KNOWN_INIT_FAILURE_MODES = ("fail", "wait", "continue")


def _run_scenario(tp: Toxiproxy, run: Dict[str, Any], api_url: str) -> tuple[int, int, List[str]]:
    tp.clear_toxics("sse")
    tp.clear_toxics("http")
    tp.set_enabled("sse", True)
    tp.set_enabled("http", True)

    probe = ChaosProbe()
    setup = run.get("setup") or {}
    user_callback_mode = setup.get("user_callback")
    wall_clock = float(setup.get("wall_clock_seconds") or 30) * 1000.0
    # Init-failure vocabulary (scenario 00; chaos README "Init-failure
    # vocabulary"): t=0 is taken BEFORE the init call, which runs on its own
    # thread so the tick loop keeps running while it blocks, and at_ms <= 0
    # injects land before the first fetch.
    init_failure_mode = setup.get("on_init_failure")
    if init_failure_mode is not None and init_failure_mode not in _KNOWN_INIT_FAILURE_MODES:
        raise ValueError(f"setup.on_init_failure={init_failure_mode!r} is not a known mode")
    catch_init_failure = bool(setup.get("catch_init_failure"))
    init_timeout_ms = int(setup.get("init_timeout_ms") or 15_000)
    if init_failure_mode == "fail" and not catch_init_failure:
        # The runner can only observe a fail-mode client it kept.
        raise ValueError("setup.on_init_failure=fail needs catch_init_failure: true")

    # Attach a logger tap for sdkLog assertions.
    quonfig_logger = logging.getLogger("quonfig")
    prev_level = quonfig_logger.level
    prev_propagate = quonfig_logger.propagate
    quonfig_logger.setLevel(logging.DEBUG)
    quonfig_logger.propagate = False  # don't double-print to root
    tap = _LogTap(probe)
    quonfig_logger.addHandler(tap)

    api_http = f"http://127.0.0.1:{CHAOS_HTTP_PORT}"
    sse_url = f"http://127.0.0.1:{CHAOS_SSE_PORT}/api/v2/sse/config"

    def _user_config_update_cb() -> None:
        probe.on_config_update()
        if user_callback_mode == "throw":
            raise RuntimeError("simulated user-callback throw for chaos scenario 10")

    client: Optional[Quonfig] = None
    # A chaos event that fails to apply (unknown shape, toxiproxy error) fails
    # the scenario: a fault that never landed must not pass (qfg-6vno.5).
    event_errors: List[str] = []
    injection_states: Dict[str, Any] = {}

    def _apply_event(ev: Dict[str, Any], at: int) -> None:
        if "inject" in ev and ev["inject"]:
            st = _apply_inject(tp, ev["inject"])
            if ev["inject"].get("name"):
                injection_states[ev["inject"]["name"]] = st
            print(f"[{at}ms] inject {ev['inject']}")
        elif "clear" in ev and ev["clear"]:
            _clear_inject(tp, injection_states.get(ev["clear"]))
            injection_states.pop(ev["clear"], None)
            print(f"[{at}ms] clear {ev['clear']}")
        elif "process" in ev and ev["process"]:
            _apply_process(tp, ev["process"])
            print(f"[{at}ms] process {ev['process']}")
        else:
            raise ValueError(f"unknown chaos event {ev!r}")

    def _schedule(ev: Dict[str, Any], offset_ms: float) -> None:
        at = int(ev.get("at_ms") or 0)

        def _fire() -> None:
            try:
                _apply_event(ev, at)
            except Exception as e:
                event_errors.append(f"[{at}ms] chaos event {ev!r} failed: {e}")
                print(event_errors[-1])

        t = threading.Timer(max(0.0, (at - offset_ms) / 1000.0), _fire)
        t.daemon = True
        t.start()

    try:
        events = run.get("chaos") or []
        pre_init: List[Dict[str, Any]] = []
        if init_failure_mode is not None:
            pre_init = [ev for ev in events if int(ev.get("at_ms") or 0) <= 0]
            events = [ev for ev in events if int(ev.get("at_ms") or 0) > 0]
            baseline_ms = int(time.time() * 1000)
            # Applied synchronously: the toxiproxy admin calls block until the
            # fault is in place, before the client is built.
            for ev in pre_init:
                _apply_event(ev, 0)

        kwargs: Dict[str, Any] = dict(
            sdk_key="test-backend-key",
            api_urls=[api_http],
            fallback_poll_enabled=True,
            fallback_poll_interval_ms=60000,
            init_timeout_ms=init_timeout_ms,
            on_no_default="warn",
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            on_config_update=_user_config_update_cb,
        )
        if init_failure_mode is not None:
            kwargs["on_init_failure"] = init_failure_mode
        client = Quonfig(**kwargs)
        probe.set_client(client)

        # Test seam: route SSE through chaos port BEFORE init() starts SSE
        # (mirrors sdk-node setting transport.__testStreamUrlOverride).
        if client._transport is not None:
            client._transport._Transport__test_stream_url_override = sse_url  # type: ignore[attr-defined]

        def _init() -> None:
            try:
                client.init()  # type: ignore[union-attr]
                probe.set_init_state("returned")
            except Exception as e:
                probe.log("error", f"init raised: {e}")
                probe.set_init_state("raised")

        if init_failure_mode is not None:
            threading.Thread(target=_init, daemon=True, name="chaos-init").start()
            for ev in events:
                _schedule(ev, int(time.time() * 1000) - baseline_ms)
        else:
            _init()
            if probe.init_state != "returned":
                event_errors.append("client.init() raised in a scenario that does not set it up")
            baseline_ms = int(time.time() * 1000)
            for ev in events:
                _schedule(ev, 0)

        # Track expectation states. SKIPPED is shape-based (a whole-expression
        # server_metric / missing SDK counter), so it is read off a blank probe
        # once, up front.
        states: List[ExpState] = [
            ExpState(i, e) for i, e in enumerate(run.get("expectations") or [])
        ]
        skip_shape = [_evaluate(s.exp["assert"], ChaosProbe()).status == "skip" for s in states]

        def _eval_fn(expr: str):  # noqa: ANN202
            def _f() -> tuple[str, str, List[str]]:
                r = _evaluate(expr, probe)
                return r.status, r.reason, r.skipped

            return _f

        poll_interval = CHAOS_POLL_MS / 1000.0
        while (int(time.time() * 1000) - baseline_ms) < wall_clock:
            elapsed = int(time.time() * 1000) - baseline_ms
            for s, shape in zip(states, skip_shape):
                step(s, elapsed, _eval_fn(s.exp["assert"]), skip_shape=shape)
            if all(s.terminal for s in states):
                break
            time.sleep(poll_interval)

        for s in states:
            if not s.passed and not s.skipped:
                s.failed = True

        details: List[str] = []
        passed = failed = skipped = 0
        for s in states:
            exp = s.exp
            label = (
                f"exp[{s.idx}] after={exp.get('after_ms') or 0}ms within={exp['within_ms']}ms "
                f"hold={exp.get('must_hold_for_ms') or 0}ms: {exp['assert']}"
            )
            if s.skipped:
                skipped += 1
                details.append(f"SKIP  {label} — {s.last_reason}")
            elif s.passed:
                passed += 1
                suffix = f" (hit at {s.hit_at}ms, passed at {s.passed_at}ms)"
                if s.skipped_leaves:
                    suffix += f" [skipped leaves: {len(s.skipped_leaves)}]"
                details.append(f"PASS  {label}{suffix}")
            else:
                failed += 1
                details.append(f"FAIL  {label} — last: {s.last_reason}")
            for leaf in s.skipped_leaves:
                details.append(f"      SKIP leaf: {leaf}")
        for err in event_errors:
            failed += 1
            details.append(f"FAIL  {err}")
        diag = probe.diagnostics()
        details.append(
            f"summary: {passed} passed, {failed} failed, {skipped} skipped "
            f"(state={probe.connection_state()}, initState={probe.init_state}, "
            f"restartLayer1={diag['worker_restart_layer1']}, "
            f"sseAttempts={diag['sse_connect_attempts']}, "
            f"fallback={probe.fallback_poller_active()}, lastRefreshMs={probe.last_refresh_ms()})"
        )
        return passed, failed, details

    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        quonfig_logger.removeHandler(tap)
        quonfig_logger.setLevel(prev_level)
        quonfig_logger.propagate = prev_propagate


# ----- collection -----


def _scenario_files() -> List[Path]:
    if not _SCENARIO_DIR.is_dir():
        return []
    return sorted(p for p in _SCENARIO_DIR.glob("*.yaml"))


def _scenario_number(path: Path) -> str:
    name = path.name
    i = name.find("-")
    return name[:i] if i > 0 else name


def _load_runs() -> List[tuple[Path, Dict[str, Any]]]:
    out: List[tuple[Path, Dict[str, Any]]] = []
    for f in _scenario_files():
        num = _scenario_number(f)
        if CHAOS_ONLY and num not in CHAOS_ONLY:
            continue
        if num in CHAOS_SKIP:
            continue
        scenario = yaml.safe_load(f.read_text())
        for run in scenario.get("tests") or []:
            validate_expectations(run, f.name)
            out.append((f, run))
    return out


@pytest.fixture(scope="module")
def toxiproxy_ready() -> Toxiproxy:
    api_url = os.environ.get("CHAOS_API_DELIVERY_URL")
    if not api_url:
        pytest.skip("CHAOS_API_DELIVERY_URL not set — run scripts/run-chaos.sh to boot the harness")
    tp = Toxiproxy(TOXIPROXY_URL)
    if not tp.ping():
        pytest.skip(f"toxiproxy not reachable at {TOXIPROXY_URL} — run scripts/run-chaos.sh first")
    upstream_host = _env("CHAOS_UPSTREAM_HOST", "host.docker.internal")
    upstream_port = int(urlparse(api_url).port or 6550)
    tp.upsert_proxy("sse", "0.0.0.0:18550", f"{upstream_host}:{upstream_port}")
    tp.upsert_proxy("http", "0.0.0.0:18551", f"{upstream_host}:{upstream_port}")
    return tp


_RUNS = _load_runs()


@pytest.mark.parametrize(
    "scenario_path, run",
    _RUNS,
    ids=[f"{p.name}::{r['name']}" for p, r in _RUNS] if _RUNS else [],
)
def test_chaos_scenario(
    toxiproxy_ready: Toxiproxy, scenario_path: Path, run: Dict[str, Any]
) -> None:
    api_url = os.environ["CHAOS_API_DELIVERY_URL"]
    passed, failed, details = _run_scenario(toxiproxy_ready, run, api_url)
    for line in details:
        print(line)
    assert failed == 0, f"{failed} expectation(s) failed"
