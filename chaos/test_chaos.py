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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import pytest
import requests
import yaml

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
        # toxiproxy is TCP-only; HTTP status injection is a no-op here.
        print(
            f"inject: sse_http_status={inj['sse_http_status']} not supported (toxiproxy TCP-only)"
        )
        return {}
    if inj.get("proxy") and inj.get("toxic"):
        toxic = inj["toxic"]
        tp.add_toxic(
            inj["proxy"], name, str(toxic.get("type")), "downstream", toxic.get("attributes") or {}
        )
        return {"proxy": inj["proxy"], "toxic": name}
    return None


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
        print(f"process: unknown action {p.get('action')!r} — no-op")


# ----- SDK probe -----


class _LogTap(logging.Handler):
    """Captures all `quonfig.*` log records into the probe's log buffer."""

    def __init__(self, probe: "ChaosProbe") -> None:
        super().__init__(level=logging.DEBUG)
        self._probe = probe

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._probe.log(record.levelname, record.getMessage())
        except Exception:
            pass


class ChaosProbe:
    """Mirrors sdk-node's ChaosProbe and sdk-go's chaosProbe vocabulary."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.conn_state = (
            "initializing"  # initializing|connected|reconnecting|falling_back|disconnected
        )
        self.last_refresh_ms = 0
        self.conn_attempts = 0
        self.restart_layer1 = 0
        self.restart_layer2 = 0
        self.fallback_active = False
        self.process_crashed = False
        self.logs: List[str] = []
        self.log_lock = threading.Lock()

    def on_sse_state(self, state: str) -> None:
        with self.lock:
            if state == "connected":
                self.conn_state = "connected"
                self.conn_attempts += 1
            elif state in ("error", "connecting"):
                if self.conn_state == "connected":
                    # connected → not-connected edge counts as a Layer 1 restart
                    self.restart_layer1 += 1
                self.conn_state = "reconnecting"
            elif state == "disconnected":
                self.conn_state = "disconnected"

    def on_config_update(self) -> None:
        with self.lock:
            self.last_refresh_ms = int(time.time() * 1000)

    def set_fallback_active(self, active: bool) -> None:
        with self.lock:
            self.fallback_active = active
            if active:
                self.conn_state = "falling_back"

    def log(self, level: str, msg: str) -> None:
        line = f"level={level.lower()} {msg}"
        with self.log_lock:
            self.logs.append(line)
        # Map onConfigUpdate-callback-throw to a Layer 1 restart so chaos
        # scenario 10 sees worker_restart_total increment per panic. Mirrors
        # sdk-node's chaos probe.
        if re.search(r"onConfigUpdate callback threw", msg, re.I):
            with self.lock:
                self.restart_layer1 += 1

    def sdk_metric(self, name: str, labels: Dict[str, str]) -> float:
        with self.lock:
            if name == "quonfig_sdk_worker_restart_total":
                if labels.get("layer") == "1":
                    return float(self.restart_layer1)
                if labels.get("layer") == "2":
                    return float(self.restart_layer2)
                return float(self.restart_layer1 + self.restart_layer2)
            if name == "quonfig_sse_connect_attempts_total":
                return float(self.conn_attempts)
        return 0.0

    def log_matches(self, level: str, regex: re.Pattern) -> int:
        with self.log_lock:
            n = 0
            for line in self.logs:
                if level and f"level={level.lower()}" not in line.lower():
                    continue
                if regex.search(line):
                    n += 1
            return n


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
    r"^client\.sdkLog\(\s*'([^']+)'\s*,\s*/(.+)/i\s*\)\s*(>=|<=|==|!=|<|>)\s*(\d+)$"
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


def _eval_leaf(
    expr: str, probe: ChaosProbe, server_metric: Callable[[str], float]
) -> tuple[bool, str]:
    expr = expr.strip()
    m = _RE_CONN_STATE_EQ.match(expr)
    if m:
        op, want = m.group(1), m.group(2)
        with probe.lock:
            got = probe.conn_state
        ok = (got == want) if op == "==" else (got != want)
        return ok, f"connectionState={got} {op} {want}"
    m = _RE_FALLBACK_EQ.match(expr)
    if m:
        want = m.group(1) == "true"
        with probe.lock:
            got = probe.fallback_active
        return got == want, f"fallbackPollerActive={got} want {want}"
    m = _RE_PROC_ALIVE_EQ.match(expr)
    if m:
        want = m.group(1) == "true"
        alive = not probe.process_crashed
        return alive == want, f"processStillAlive={alive} want {want}"
    m = _RE_LAST_REFRESH.match(expr)
    if m:
        op = m.group(1)
        ago = int(m.group(2))
        with probe.lock:
            last = probe.last_refresh_ms
        threshold = int(time.time() * 1000) - ago
        ok = _compare(op, last, threshold)
        return ok, f"lastSuccessfulRefresh={last} {op} (now()-{ago})={threshold}"
    m = _RE_SDK_METRIC.match(expr)
    if m:
        metric, layer, op, want_str = m.group(1), m.group(2), m.group(3), m.group(4)
        labels = {"layer": layer} if layer else {}
        got = probe.sdk_metric(metric, labels)
        want = float(want_str)
        ok = _compare(op, got, want)
        return ok, f"sdkMetric({metric},layer={layer or ''})={got} {op} {want}"
    m = _RE_SERVER_METRIC.match(expr)
    if m:
        name, op, want_str = m.group(1), m.group(2), m.group(3)
        got = server_metric(name)
        want = float(want_str)
        ok = _compare(op, got, want)
        return ok, f"server_metric({name})={got} {op} {want}"
    m = _RE_SDK_LOG.match(expr)
    if m:
        level, pattern, op, want_str = m.group(1), m.group(2), m.group(3), m.group(4)
        regex = re.compile(pattern, re.I)
        got = probe.log_matches(level, regex)
        want = float(want_str)
        ok = _compare(op, got, want)
        return ok, f"sdkLog({level},/{pattern}/i)={got} {op} {want}"
    return False, f"unrecognized expression: {expr}"


def _evaluate(
    expr: str, probe: ChaosProbe, server_metric: Callable[[str], float]
) -> tuple[bool, str]:
    expr = expr.strip()
    if not expr:
        return True, ""
    if " OR " in expr:
        parts = _split_outside_quotes_and_regex(expr, " OR ")
        reasons: List[str] = []
        for p in parts:
            ok, why = _evaluate(p, probe, server_metric)
            if ok:
                return True, ""
            reasons.append(why)
        return False, "OR: " + " | ".join(reasons)
    if " AND " in expr:
        parts = _split_outside_quotes_and_regex(expr, " AND ")
        for p in parts:
            ok, why = _evaluate(p, probe, server_metric)
            if not ok:
                return False, "AND: " + why
        return True, ""
    return _eval_leaf(expr, probe, server_metric)


# ----- runner -----


def _run_scenario(tp: Toxiproxy, run: Dict[str, Any], api_url: str) -> tuple[int, int, List[str]]:
    tp.clear_toxics("sse")
    tp.clear_toxics("http")
    tp.set_enabled("sse", True)
    tp.set_enabled("http", True)

    probe = ChaosProbe()
    setup = run.get("setup") or {}
    user_callback_mode = setup.get("user_callback")
    wall_clock = float(setup.get("wall_clock_seconds") or 30) * 1000.0

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
        if user_callback_mode == "throw":
            raise RuntimeError("simulated user-callback throw for chaos scenario 10")

    client: Optional[Quonfig] = None
    fallback_watcher_stop = threading.Event()
    fallback_watcher: Optional[threading.Thread] = None

    try:
        client = Quonfig(
            sdk_key="test-backend-key",
            api_urls=[api_http],
            fallback_poll_enabled=True,
            fallback_poll_interval_ms=60000,
            init_timeout=15.0,
            on_no_default="warn",
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            on_sse_connection_state_change=lambda s: probe.on_sse_state(s),
            on_config_update=lambda: (probe.on_config_update(), _user_config_update_cb()),
        )

        # Test seam: route SSE through chaos port BEFORE init() starts SSE
        # (mirrors sdk-node setting transport.__testStreamUrlOverride).
        if client._transport is not None:
            client._transport._Transport__test_stream_url_override = sse_url  # type: ignore[attr-defined]

        def _watch_fallback() -> None:
            while not fallback_watcher_stop.is_set():
                if client is not None:
                    active = client.fallback_poller_active()
                    with probe.lock:
                        prev = probe.fallback_active
                    if active != prev:
                        probe.set_fallback_active(active)
                fallback_watcher_stop.wait(0.1)

        fallback_watcher = threading.Thread(target=_watch_fallback, daemon=True)
        fallback_watcher.start()

        try:
            client.init()
        except Exception as e:
            probe.process_crashed = True
            probe.log("error", f"init failed: {e}")

        baseline_ms = int(time.time() * 1000)

        # Schedule chaos events.
        injection_states: Dict[str, Any] = {}

        def _schedule(ev: Dict[str, Any]) -> None:
            at = int(ev.get("at_ms") or 0)

            def _fire() -> None:
                try:
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
                except Exception as e:
                    print(f"[{at}ms] chaos event failed: {e}")

            t = threading.Timer(at / 1000.0, _fire)
            t.daemon = True
            t.start()

        for ev in run.get("chaos") or []:
            _schedule(ev)

        # Track expectation states.
        states: List[Dict[str, Any]] = [
            {
                "idx": i,
                "exp": e,
                "passed": False,
                "failed": False,
                "hit_at": None,
                "held_since": None,
                "last_reason": "",
            }
            for i, e in enumerate(run.get("expectations") or [])
        ]

        def _server_metric(_name: str) -> float:
            return 0.0

        poll_interval = CHAOS_POLL_MS / 1000.0
        while (int(time.time() * 1000) - baseline_ms) < wall_clock:
            elapsed = int(time.time() * 1000) - baseline_ms
            all_terminal = True
            for s in states:
                if s["passed"] or s["failed"]:
                    continue
                ok, why = _evaluate(s["exp"]["assert"], probe, _server_metric)
                s["last_reason"] = why
                if ok:
                    if s["held_since"] is None:
                        s["held_since"] = int(time.time() * 1000)
                        s["hit_at"] = elapsed
                    hold_for = int(s["exp"].get("must_hold_for_ms") or 0)
                    if hold_for <= 0 or (int(time.time() * 1000) - s["held_since"]) >= hold_for:
                        s["passed"] = True
                else:
                    s["held_since"] = None
                if not s["passed"] and elapsed > int(s["exp"]["within_ms"]):
                    s["failed"] = True
                if not s["passed"] and not s["failed"]:
                    all_terminal = False
            if all_terminal:
                break
            time.sleep(poll_interval)

        for s in states:
            if not s["passed"]:
                s["failed"] = True

        details: List[str] = []
        passed = failed = 0
        for s in states:
            exp = s["exp"]
            label = (
                f"exp[{s['idx']}] within={exp['within_ms']}ms "
                f"hold={exp.get('must_hold_for_ms') or 0}ms: {exp['assert']}"
            )
            if s["passed"]:
                passed += 1
                details.append(f"PASS  {label} (hit at {s['hit_at']}ms)")
            else:
                failed += 1
                details.append(f"FAIL  {label} — last: {s['last_reason']}")
        with probe.lock:
            details.append(
                f"summary: {passed} passed, {failed} failed "
                f"(state={probe.conn_state}, restartLayer1={probe.restart_layer1}, "
                f"fallback={probe.fallback_active}, lastRefreshMs={probe.last_refresh_ms})"
            )
        return passed, failed, details

    finally:
        fallback_watcher_stop.set()
        if fallback_watcher is not None:
            fallback_watcher.join(timeout=2.0)
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
