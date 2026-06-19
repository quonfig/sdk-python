"""Failover + canonical-ordering chaos runners for sdk-python (qfg-7h5d.1.8).

Mirrors sdk-go's failover_chaos_test.go. Two runners consume the shared corpus
rigs in ``integration-test-data/chaos``:

  scenarios-failover/  (f01-f05) — ONE fixture upstream behind TWO proxies (the
    primary 'http' leg + the 'secondary' leg). Faults hit the primary leg only;
    the SDK must fail the HTTP config fetch over to the secondary and keep
    serving, fast (well inside the init budget). SSE is asserted to NOT repoint.

  scenarios-ordering/  (o01-o04) — TWO fixture upstreams pinned to divergent
    Meta.generations. The SDK must end up holding the higher generation and an
    established client must never regress to an older one.

Like the sdk-go runner, each test spawns its own api-delivery fixture
upstream(s) (built with ``GOWORK=off``) and repoints the seeded
``http``/``secondary``/``sse`` toxiproxy proxies at them — spawning here (rather
than via the launcher) lets the ordering runner pin a different generation per
scenario. Only toxiproxy needs to be running; boot it with
``scripts/run-failover-chaos.sh`` (which calls the shared start-chaos.sh).

RED baseline (proven red->green by this bead's focused unit tests, see
tests/unit/test_config_fetch_timeout.py and tests/unit/test_reject_older_guard.py):
  - f02 (primary hang) is RED without the per-URL config-fetch timeout — a hung
    primary starves the secondary until the init timeout.
  - o02 (secondary older) is RED without the reject-older install guard — a
    failover fetch of the older secondary regresses the held generation.

o01-secondary-newer is SKIPPED (CHAOS_SKIP) — it needs cross-leg "max-wins"
(hold the higher generation even when the older primary leg is healthy), which
is out of the §5f reject-older scope ("no source ranking") and owned by
qfg-7h5d.1.14. This mirrors the sdk-go pilot's CI gate (f01-f05 + o02-o04).

Excluded from default pytest collection (testpaths = ["tests"]); invoke via
``pytest chaos/test_failover_chaos.py`` or the wrapper script.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest
import requests
import yaml

from quonfig import Quonfig

# ----- paths -----

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_FAILOVER_DIR = _REPO_ROOT / "integration-test-data" / "chaos" / "scenarios-failover"
_ORDERING_DIR = _REPO_ROOT / "integration-test-data" / "chaos" / "scenarios-ordering"
_API_DELIVERY_DIR = _REPO_ROOT / "api-delivery"
_FIXTURE_DIR = _REPO_ROOT / "integration-test-data" / "data" / "integration-tests"
_FIXTURE_KEYS = _API_DELIVERY_DIR / "testdata" / "fixture-sdk-keys.json"


# ----- env knobs -----


def _env(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val else default


TOXIPROXY_URL = _env("TOXIPROXY_URL", "http://127.0.0.1:8474")
UPSTREAM_HOST = _env("CHAOS_UPSTREAM_HOST", "host.docker.internal")
# Host ports the launcher maps the seeded proxies to (docker-compose.yml).
RIG_PRIMARY_PORT = int(_env("HTTP_PROXY_PORT", "18551"))  # 'http' proxy — primary HTTP leg
RIG_SECONDARY_PORT = int(_env("SECONDARY_PROXY_PORT", "18552"))  # 'secondary' proxy
RIG_SSE_PORT = int(_env("SSE_PROXY_PORT", "18550"))  # 'sse' proxy — primary leg only
RIG_INIT_TIMEOUT_MS = int(_env("CHAOS_INIT_TIMEOUT_MS", "8000"))


def _split_csv(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


# CHAOS_SKIP matches scenario numbers (e.g. "o01"). Mirrors the sdk-go CHAOS_SKIP
# knob that skips TestOrderingChaos/o01-secondary-newer.
CHAOS_ONLY = _split_csv(os.environ.get("CHAOS_ONLY"))
CHAOS_SKIP = _split_csv(os.environ.get("CHAOS_SKIP"))


# ----- toxiproxy admin client -----


class Toxiproxy:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self._sess = requests.Session()

    def ping(self) -> bool:
        try:
            return self._sess.get(f"{self.base}/version", timeout=5).ok
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


# ----- api-delivery fixture upstream(s) -----


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _dial_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


_BUILD_LOCK = threading.Lock()
_BUILT_BINARY: Optional[str] = None


def _build_api_delivery() -> str:
    """Build the api-delivery server binary once (GOWORK=off so the pinned
    sdk-go module resolves). Returns the binary path."""
    global _BUILT_BINARY
    with _BUILD_LOCK:
        if _BUILT_BINARY is not None:
            return _BUILT_BINARY
        if not _API_DELIVERY_DIR.is_dir():
            pytest.skip(f"api-delivery not checked out at {_API_DELIVERY_DIR}")
        binary = str(_HERE / ".chaos-api-delivery-failover")
        env = {**os.environ, "GOWORK": "off"}
        proc = subprocess.run(
            ["go", "build", "-o", binary, "./cmd/server"],
            cwd=str(_API_DELIVERY_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            pytest.skip(f"go build api-delivery failed: {proc.stderr or proc.stdout}")
        _BUILT_BINARY = binary
        return binary


class _Upstream:
    """A spawned api-delivery fixture process pinned to a Meta.generation."""

    def __init__(self, binary: str, port: int, generation: int) -> None:
        env = {
            **os.environ,
            "PORT": str(port),
            "FIXTURE_DIR": str(_FIXTURE_DIR),
            "SDK_KEYS_FILE": str(_FIXTURE_KEYS),
            "QUONFIG_ENVIRONMENT": "development",
            "SSE_HEARTBEAT_INTERVAL": "1s",
            "FIXTURE_GENERATION": str(generation),
        }
        self.port = port
        self.generation = generation
        self._proc = subprocess.Popen(  # noqa: S603
            [binary], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if _dial_ok("127.0.0.1", port):
                time.sleep(0.1)
                return
            if self._proc.poll() is not None:
                raise RuntimeError(f"api-delivery (gen={generation}) exited early on :{port}")
            time.sleep(0.05)
        self.close()
        raise RuntimeError(f"api-delivery (gen={generation}) did not start on :{port} within 15s")

    def close(self) -> None:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


def _reconfigure_rig_proxies(tp: Toxiproxy, primary_port: int, secondary_port: int) -> None:
    """Repoint the seeded proxies at the spawned upstream(s). The SSE leg always
    tracks the primary upstream (failover is HTTP-only)."""
    tp.upsert_proxy("http", f"0.0.0.0:{RIG_PRIMARY_PORT}", f"{UPSTREAM_HOST}:{primary_port}")
    tp.upsert_proxy(
        "secondary", f"0.0.0.0:{RIG_SECONDARY_PORT}", f"{UPSTREAM_HOST}:{secondary_port}"
    )
    tp.upsert_proxy("sse", f"0.0.0.0:{RIG_SSE_PORT}", f"{UPSTREAM_HOST}:{primary_port}")


# ----- SDK probe -----


class ChaosProbe:
    """Reads the failover + canonical-ordering accessors added for this epic
    (qfg-7h5d.1.8). Nil-safe before the client is constructed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: Optional[Quonfig] = None

    def set_client(self, c: Quonfig) -> None:
        with self._lock:
            self._client = c

    def _c(self) -> Optional[Quonfig]:
        with self._lock:
            return self._client

    def ready(self) -> bool:
        c = self._c()
        return bool(c and c.ready())

    def resolved_from(self) -> str:
        c = self._c()
        return c.resolved_from() if c else ""

    def held_generation(self) -> int:
        c = self._c()
        return c.held_generation() if c else 0

    def config_install_count(self) -> int:
        c = self._c()
        return c.config_install_count() if c else 0

    def sse_failed_over_to_secondary(self) -> bool:
        c = self._c()
        return bool(c and c.sse_failed_over_to_secondary())


# ----- expression evaluator (failover/ordering vocabulary) -----

_RE_READY = re.compile(r"^client\.ready\(\)\s*==\s*(true|false)$")
_RE_RESOLVED = re.compile(r"^client\.resolvedFrom\(\)\s*(==|!=)\s*'([^']+)'$")
_RE_HELD_GEN = re.compile(r"^client\.heldGeneration\(\)\s*(>=|<=|==|!=|<|>)\s*(-?\d+)$")
_RE_INSTALL = re.compile(r"^client\.configInstallCount\(\)\s*(>=|<=|==|!=|<|>)\s*(-?\d+)$")
_RE_SSE_FAILOVER = re.compile(r"^client\.sseFailedOverToSecondary\(\)\s*==\s*(true|false)$")


def _compare(op: str, a: float, b: float) -> bool:
    return {
        "==": a == b,
        "!=": a != b,
        "<": a < b,
        "<=": a <= b,
        ">": a > b,
        ">=": a >= b,
    }.get(op, False)


def _eval_leaf(expr: str, probe: ChaosProbe) -> Tuple[bool, str]:
    expr = expr.strip()
    m = _RE_READY.match(expr)
    if m:
        want = m.group(1) == "true"
        got = probe.ready()
        return got == want, f"ready={got} want {want}"
    m = _RE_RESOLVED.match(expr)
    if m:
        op, want = m.group(1), m.group(2)
        got = probe.resolved_from()
        ok = (got == want) if op == "==" else (got != want)
        return ok, f"resolvedFrom={got!r} {op} {want!r}"
    m = _RE_HELD_GEN.match(expr)
    if m:
        op, want = m.group(1), int(m.group(2))
        got = probe.held_generation()
        return _compare(op, got, want), f"heldGeneration={got} {op} {want}"
    m = _RE_INSTALL.match(expr)
    if m:
        op, want = m.group(1), int(m.group(2))
        got = probe.config_install_count()
        return _compare(op, got, want), f"configInstallCount={got} {op} {want}"
    m = _RE_SSE_FAILOVER.match(expr)
    if m:
        want = m.group(1) == "true"
        got = probe.sse_failed_over_to_secondary()
        return got == want, f"sseFailedOverToSecondary={got} want {want}"
    return False, f"unrecognized expression: {expr}"


def _evaluate(expr: str, probe: ChaosProbe) -> Tuple[bool, str]:
    expr = expr.strip()
    if not expr:
        return True, ""
    if " OR " in expr:
        reasons = []
        for p in expr.split(" OR "):
            ok, why = _evaluate(p, probe)
            if ok:
                return True, ""
            reasons.append(why)
        return False, "OR: " + " | ".join(reasons)
    if " AND " in expr:
        for p in expr.split(" AND "):
            ok, why = _evaluate(p, probe)
            if not ok:
                return False, "AND: " + why
        return True, ""
    return _eval_leaf(expr, probe)


# ----- chaos injection (self-restoring failover-rig aliases) -----


def _restore_after(ms: int, fn: Callable[[], None]) -> None:
    def _run() -> None:
        time.sleep(ms / 1000.0)
        try:
            fn()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _apply_inject(tp: Toxiproxy, inj: Dict[str, Any]) -> None:
    """Map a failover-rig inject alias to a self-restoring toxiproxy action on
    the primary HTTP leg (or the SSE leg). Each alias carries its own duration."""
    name = inj.get("name") or "primary_fault"
    if "primary_refused_ms" in inj:
        tp.set_enabled("http", False)
        _restore_after(int(inj["primary_refused_ms"]), lambda: tp.set_enabled("http", True))
    elif "primary_hang_ms" in inj:
        ms = int(inj["primary_hang_ms"])
        tp.add_toxic("http", name, "timeout", "downstream", {"timeout": ms})
        _restore_after(ms, lambda: tp.remove_toxic("http", name))
    elif "primary_latency_ms" in inj:
        ms = int(inj["primary_latency_ms"])
        tp.add_toxic("http", name, "latency", "downstream", {"latency": ms})
        _restore_after(ms, lambda: tp.remove_toxic("http", name))
    elif "sse_down_ms" in inj:
        tp.set_enabled("sse", False)
        _restore_after(int(inj["sse_down_ms"]), lambda: tp.set_enabled("sse", True))
    else:
        print(f"_apply_inject: unhandled inject shape {inj} — no-op")


# ----- scenario runner -----


def _run_rig_scenario(
    tp: Toxiproxy, run: Dict[str, Any], drive_refresh: bool
) -> Tuple[int, int, List[str]]:
    # Start from a clean proxy state — no leftover toxics, all legs enabled.
    for p in ("http", "secondary", "sse"):
        tp.clear_toxics(p)
        tp.set_enabled(p, True)

    setup = run.get("setup") or {}
    sse_endpoint = setup.get("sse_endpoint") or "disabled"
    sse_enabled = sse_endpoint not in ("", "disabled")
    wall_clock = float(setup.get("wall_clock_seconds") or 30)

    probe = ChaosProbe()
    primary_url = f"http://127.0.0.1:{RIG_PRIMARY_PORT}"
    secondary_url = f"http://127.0.0.1:{RIG_SECONDARY_PORT}"
    sse_proxy_url = f"http://127.0.0.1:{RIG_SSE_PORT}/api/v2/sse/config"
    # A definitely-closed port so SSE never connects when the scenario disables
    # it (failover f01-f04) or when we want SSE failure to engage the ordering
    # rig's fallback-poll refresh loop.
    dead_sse_url = f"http://127.0.0.1:{_free_port()}/api/v2/sse/config"

    client: Optional[Quonfig] = None
    try:
        client = Quonfig(
            sdk_key="test-backend-key",
            api_urls=[primary_url, secondary_url],
            init_timeout_ms=RIG_INIT_TIMEOUT_MS,
            on_init_failure="return_zero_value",
            collect_evaluation_summaries=False,
            context_upload_mode="none",
            # Ordering rig: a short fallback-poll interval models ongoing config
            # polling — SSE points at a dead port so it fails initial-connect and
            # engages the poller, whose primary-first failover fetch exercises the
            # reject-older guard (o02) and heal-forward (o03). Failover rig: poller
            # off, so resolvedFrom is determined solely by the initial fetch.
            fallback_poll_enabled=drive_refresh,
            fallback_poll_interval_ms=500,
        )
        # Route SSE through the chaos seam BEFORE init() starts the SSE loop
        # (mirrors test_chaos.py). f05 points at the live sse proxy; everything
        # else points at a dead port.
        if client._transport is not None:
            override = sse_proxy_url if sse_enabled else dead_sse_url
            client._transport._Transport__test_stream_url_override = override  # type: ignore[attr-defined]

        probe.set_client(client)
        try:
            client.init()
        except Exception as e:  # noqa: BLE001
            print(f"client init returned: {e} — continuing (scenario may still observe it)")

        baseline = time.monotonic()

        # Schedule chaos events against the primary leg.
        for ev in run.get("chaos") or []:
            inj = ev.get("inject")
            if not inj:
                continue
            at = int(ev.get("at_ms") or 0)

            def _fire(inj: Dict[str, Any] = inj, at: int = at) -> None:
                _apply_inject(tp, inj)
                print(f"[{at}ms] inject {inj}")

            timer = threading.Timer(at / 1000.0, _fire)
            timer.daemon = True
            timer.start()

        return _eval_expectations(run, baseline, probe, wall_clock)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _eval_expectations(
    run: Dict[str, Any], baseline: float, probe: ChaosProbe, wall_clock: float
) -> Tuple[int, int, List[str]]:
    states = [
        {
            "idx": i,
            "exp": e,
            "passed": False,
            "failed": False,
            "held_since": None,
            "hit_at": None,
            "last": "",
        }
        for i, e in enumerate(run.get("expectations") or [])
    ]
    deadline = baseline + wall_clock
    while time.monotonic() < deadline:
        elapsed_ms = (time.monotonic() - baseline) * 1000.0
        all_terminal = True
        for s in states:
            if s["passed"] or s["failed"]:
                continue
            ok, why = _evaluate(s["exp"]["assert"], probe)
            s["last"] = why
            if ok:
                if s["held_since"] is None:
                    s["held_since"] = time.monotonic()
                    s["hit_at"] = elapsed_ms
                hold_for = int(s["exp"].get("must_hold_for_ms") or 0)
                if hold_for <= 0 or (time.monotonic() - s["held_since"]) * 1000.0 >= hold_for:
                    s["passed"] = True
            else:
                s["held_since"] = None
            if not s["passed"] and elapsed_ms > int(s["exp"]["within_ms"]):
                s["failed"] = True
            if not s["passed"] and not s["failed"]:
                all_terminal = False
        if all_terminal:
            break
        time.sleep(0.2)

    details: List[str] = []
    passed = failed = 0
    for s in states:
        if not s["passed"]:
            s["failed"] = True
        exp = s["exp"]
        label = (
            f"exp[{s['idx']}] within={exp['within_ms']}ms "
            f"hold={exp.get('must_hold_for_ms') or 0}ms: {exp['assert']}"
        )
        if s["passed"]:
            passed += 1
            details.append(f"PASS  {label} (hit at {int(s['hit_at'] or 0)}ms)")
        else:
            failed += 1
            details.append(f"FAIL  {label} — last: {s['last']}")
    details.append(
        f"summary: {passed} passed, {failed} failed (ready={probe.ready()}, "
        f"resolvedFrom={probe.resolved_from()!r}, heldGeneration={probe.held_generation()}, "
        f"installs={probe.config_install_count()})"
    )
    return passed, failed, details


# ----- collection -----


def _scenario_number(path: Path) -> str:
    name = path.name
    i = name.find("-")
    return name[:i] if i > 0 else name


def _load_runs(scenario_dir: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    out: List[Tuple[Path, Dict[str, Any]]] = []
    if not scenario_dir.is_dir():
        return out
    for f in sorted(scenario_dir.glob("*.yaml")):
        num = _scenario_number(f)
        if CHAOS_ONLY and num not in CHAOS_ONLY:
            continue
        if num in CHAOS_SKIP:
            continue
        scenario = yaml.safe_load(f.read_text())
        for run in scenario.get("tests") or []:
            out.append((f, run))
    return out


_FAILOVER_RUNS = _load_runs(_FAILOVER_DIR)
_ORDERING_RUNS = _load_runs(_ORDERING_DIR)


@pytest.fixture(scope="module")
def rig() -> Toxiproxy:
    tp = Toxiproxy(TOXIPROXY_URL)
    if not tp.ping():
        pytest.skip(
            f"toxiproxy not reachable at {TOXIPROXY_URL} — run scripts/run-failover-chaos.sh first"
        )
    return tp


@pytest.mark.parametrize(
    "scenario_path, run",
    _FAILOVER_RUNS,
    ids=[f"{p.name}::{r['name']}" for p, r in _FAILOVER_RUNS] if _FAILOVER_RUNS else [],
)
def test_failover(rig: Toxiproxy, scenario_path: Path, run: Dict[str, Any]) -> None:
    """One fixture upstream behind primary+secondary proxies; faults hit the
    primary leg, the SDK must resolve off the secondary, fast."""
    binary = _build_api_delivery()
    port = _free_port()
    upstream = _Upstream(binary, port, generation=0)
    try:
        _reconfigure_rig_proxies(rig, primary_port=port, secondary_port=port)
        passed, failed, details = _run_rig_scenario(rig, run, drive_refresh=False)
        for line in details:
            print(line)
        assert failed == 0, f"{failed} expectation(s) failed"
    finally:
        upstream.close()


@pytest.mark.parametrize(
    "scenario_path, run",
    _ORDERING_RUNS,
    ids=[f"{p.name}::{r['name']}" for p, r in _ORDERING_RUNS] if _ORDERING_RUNS else [],
)
def test_ordering(rig: Toxiproxy, scenario_path: Path, run: Dict[str, Any]) -> None:
    """Two fixture upstreams pinned to the generations declared in the
    scenario's setup.upstreams; the SDK must hold the higher generation and
    never regress an established client."""
    binary = _build_api_delivery()
    ups = (run.get("setup") or {}).get("upstreams") or []
    gens = {u["role"]: int(u["generation"]) for u in ups}
    primary_port, secondary_port = _free_port(), _free_port()
    primary = _Upstream(binary, primary_port, generation=gens.get("primary", 0))
    secondary = _Upstream(binary, secondary_port, generation=gens.get("secondary", 0))
    try:
        _reconfigure_rig_proxies(rig, primary_port=primary_port, secondary_port=secondary_port)
        passed, failed, details = _run_rig_scenario(rig, run, drive_refresh=True)
        for line in details:
            print(line)
        assert failed == 0, f"{failed} expectation(s) failed"
    finally:
        primary.close()
        secondary.close()
