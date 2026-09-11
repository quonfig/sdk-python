"""Failover telemetry emission tests (qfg-41nh.18).

The SDK emits a per-flush-window ``failover`` telemetry event carrying the
operational counters for the secondary-delivery hardening: how many config-fetch
cycles fired the parallel hedge's secondary leg, how many installs the
reject-older ordering guard dropped, and which upstream leg served each
successful HTTP install. Mirrors sdk-go's FailoverAggregator + FailoverEvent
(commit 485ce2c). The event rides any enabled telemetry stream regardless of the
eval/context opt-outs and is emitted ONLY when at least one counter is non-zero,
so a healthy steady-state client emits nothing.

The wire keys are camelCase EXACTLY as api-telemetry's Zod schema parses them,
even though the Python internals are snake_case.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from quonfig import Quonfig
from quonfig.telemetry.collectors import FailoverCollector
from quonfig.telemetry.models import TelemetryPayload
from quonfig.types import ConfigEnvelope, Meta


def test_collector_all_zero_drains_to_none() -> None:
    # A healthy steady-state client never touches a failover site, so the
    # collector must drain to None (no event on the wire at all).
    assert FailoverCollector().drain() is None


def test_collector_counts_and_wire_keys_are_camelcase() -> None:
    c = FailoverCollector()
    c.record_hedge_fired()
    c.record_hedge_fired()
    c.record_guard_rejected()
    c.record_resolved_from(0)  # primary
    c.record_resolved_from(0)  # primary
    c.record_resolved_from(1)  # secondary
    c.record_resolved_from(3)  # any index > 0 counts as secondary

    event = c.drain()
    assert event is not None

    payload = TelemetryPayload(instance_hash="test-hash", events=[event])
    doc = json.loads(json.dumps(payload.to_dict()))

    assert doc["instanceHash"] == "test-hash"
    assert len(doc["events"]) == 1

    failover = doc["events"][0]["failover"]
    # Exact camelCase key set matching sdk-go's FailoverEvent JSON tags.
    assert set(failover.keys()) == {
        "start",
        "end",
        "hedgeFired",
        "guardRejected",
        "resolvedFromPrimary",
        "resolvedFromSecondary",
        "resolvedFromLkg",
    }
    assert failover["hedgeFired"] == 2
    assert failover["guardRejected"] == 1
    assert failover["resolvedFromPrimary"] == 2
    assert failover["resolvedFromSecondary"] == 2
    assert failover["resolvedFromLkg"] == 0
    # start/end are unix MILLISECONDS (13-digit-ish integers), start <= end.
    assert isinstance(failover["start"], int)
    assert isinstance(failover["end"], int)
    assert failover["start"] > 1_000_000_000_000
    assert failover["end"] >= failover["start"]


def test_collector_negative_source_index_is_ignored() -> None:
    # SSE / datadir installs pass a negative source index (no HTTP leg served
    # them) and must not be counted as either primary or secondary.
    c = FailoverCollector()
    c.record_resolved_from(-1)
    assert c.drain() is None


def test_collector_drain_resets_window() -> None:
    c = FailoverCollector()
    c.record_guard_rejected()
    first = c.drain()
    assert first is not None
    assert first.failover is not None
    assert first.failover.guard_rejected == 1
    # After draining, the window is cleared — a second drain with no new
    # activity emits nothing.
    assert c.drain() is None


def test_reporter_flush_appends_failover_event() -> None:
    """Recording through the reporter's pass-through methods must surface a
    ``failover`` event in the POSTed payload with the correct camelCase keys."""
    from quonfig.telemetry import TelemetryReporter

    reporter = TelemetryReporter(
        telemetry_url="https://telemetry.example",
        sdk_key="qf_sk_development_0000_dead",
        instance_hash="inst-1",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )

    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

    def _fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["body"] = json
        return _FakeResp()

    reporter._session.post = _fake_post  # type: ignore[assignment]

    # No activity yet -> nothing posted.
    reporter._flush()
    assert "body" not in captured

    reporter.record_hedge_fired()
    reporter.record_guard_rejected()
    reporter.record_resolved_from(0)
    reporter.record_resolved_from(1)
    reporter._flush()

    assert captured["url"].endswith("/api/v1/telemetry/")
    events = captured["body"]["events"]
    failover_events = [e for e in events if "failover" in e]
    assert len(failover_events) == 1
    fo = failover_events[0]["failover"]
    assert fo["hedgeFired"] == 1
    assert fo["guardRejected"] == 1
    assert fo["resolvedFromPrimary"] == 1
    assert fo["resolvedFromSecondary"] == 1
    assert fo["resolvedFromLkg"] == 0


# --- Client call-site wiring -------------------------------------------------


def _envelope(generation: int) -> ConfigEnvelope:
    return ConfigEnvelope(
        configs=[],
        meta=Meta(version=f"gen-{generation}", environment="Production", generation=generation),
    )


def test_client_install_records_resolved_from_and_guard_rejected() -> None:
    """The single network-install funnel (_install_network_envelope) must record
    resolved-from on an accepted HTTP install and guard-rejected on a STRICTLY
    OLDER payload dropped by the reject-older guard — on BOTH the HTTP and SSE
    paths.

    Updated for qfg-rr5b: this test used to assert ``guard_rejected == 2``,
    counting the equal-generation SSE re-delivery below as a rejection too.
    Equal-generation re-delivery is now a silent no-op (still dropped, still
    not counted), so only the strictly-older HTTP payload counts.
    """
    # Reporter exists (context telemetry on) but we never evaluate, so only
    # failover events are produced. Full opt-out (both streams off) would leave
    # _telemetry None and emit nothing — the intended behavior, tested elsewhere.
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=["http://127.0.0.1:1", "http://127.0.0.1:2"],
        collect_evaluation_summaries=False,
        context_upload_mode="shapes_only",
        fallback_poll_enabled=False,
    )
    try:
        reporter = client._telemetry
        assert reporter is not None

        # Fresh store seeds off gen 1 served by the PRIMARY leg (index 0).
        assert client._install_network_envelope(_envelope(1), from_http=True, source_index=0)
        # A newer gen from the SECONDARY leg (index 1) heals forward.
        assert client._install_network_envelope(_envelope(2), from_http=True, source_index=1)
        # A late STRICTLY OLDER HTTP payload is dropped by the guard and counted.
        assert not client._install_network_envelope(_envelope(1), from_http=True, source_index=1)
        # An SSE message (from_http=False) at the SAME generation is dropped too,
        # but it is a re-delivery, not a backwards move: not counted, and it must
        # not move resolved_from either (qfg-rr5b).
        assert not client._install_network_envelope(_envelope(2), from_http=False)

        event = reporter._failover_collector.drain()
        assert event is not None and event.failover is not None
        fo = event.failover
        assert fo.resolved_from_primary == 1
        assert fo.resolved_from_secondary == 1
        assert fo.guard_rejected == 1
        assert fo.hedge_fired == 0
        assert fo.resolved_from_lkg == 0
    finally:
        client.close()


class _HedgeUpstream:
    """Minimal httptest-style server pinned to a Meta.generation, optionally
    delayed before it answers (mirrors tests/unit/test_hedge.py)."""

    def __init__(self, generation: int, delay_seconds: float = 0.0) -> None:
        self.generation = generation
        self.delay_seconds = delay_seconds
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if outer.delay_seconds > 0:
                    time.sleep(outer.delay_seconds)
                body = json.dumps(
                    {
                        "configs": [],
                        "meta": {
                            "version": f"gen-{outer.generation}",
                            "environment": "Production",
                            "generation": outer.generation,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("ETag", f'"gen-{outer.generation}"')
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._server.shutdown()


def test_client_records_hedge_fired_when_secondary_leg_fires() -> None:
    """A SLOW primary makes the parallel hedge fire its secondary leg; the SDK
    must record one hedge-fired for the cycle and resolve from the secondary.

    hedge_fired is recorded per-cycle only AFTER the full hedge loop drains both
    legs (the slow primary included), so we poll the collector until the cycle
    completes rather than draining the instant the fast secondary installs."""
    # Primary slow past the (shortened) hedge delay so the hedge fires; secondary
    # fast. Kept ~1s so the slow-primary leg settles quickly and the test is snappy.
    primary = _HedgeUpstream(generation=41, delay_seconds=1.0)
    secondary = _HedgeUpstream(generation=42, delay_seconds=0.0)
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=[primary.url, secondary.url],
        collect_evaluation_summaries=False,
        context_upload_mode="shapes_only",
        fallback_poll_enabled=False,
        hedge_delay_ms=250,
        init_timeout_ms=8000,
        on_init_failure="return_zero_value",
    )
    if client._transport is not None:
        client._transport._Transport__test_stream_url_override = (  # type: ignore[attr-defined]
            "http://127.0.0.1:1/api/v2/sse/config"
        )
    try:
        client.init()
        reporter = client._telemetry
        assert reporter is not None

        # Wait for the hedge cycle to complete (both legs drained), which is when
        # hedge_fired is recorded.
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if reporter._failover_collector._hedge_fired >= 1:
                break
            time.sleep(0.02)

        assert client.held_generation() == 42, "hedge should have installed the fast secondary's 42"

        event = reporter._failover_collector.drain()
        assert event is not None and event.failover is not None
        fo = event.failover
        assert fo.hedge_fired >= 1, "the slow primary should have triggered the hedge"
        assert fo.resolved_from_secondary >= 1, "the secondary leg served the held config"
    finally:
        client.close()
        primary.close()
        secondary.close()


def test_equal_generation_redelivery_is_not_counted_as_guard_rejected() -> None:
    """Only a STRICTLY older payload is a guard rejection (qfg-rr5b).

    Two server behaviors re-deliver an envelope the client already holds at the
    SAME generation: api-delivery's SSE ``sendInitialConfig`` resends the
    current envelope on every connect, and a config poll at the same generation
    returns a full 200 whenever the per-leg ETag slot is empty (a fresh
    transport, a reconnect, a new process). Both were counted as
    ``guardRejected``, which feeds the ``sdk_failover`` alerting signal where it
    is supposed to mean "a leg tried to move us backwards" — a steady-state
    client showed ``guardRejected: 1`` from init alone, plus one per SSE
    reconnect.

    Equal generation stays not-installed; it is simply not counted.
    """
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=["http://127.0.0.1:1", "http://127.0.0.1:2"],
        collect_evaluation_summaries=False,
        context_upload_mode="shapes_only",
        fallback_poll_enabled=False,
    )
    try:
        reporter = client._telemetry
        assert reporter is not None

        # Seed an established client at generation 7.
        assert client._install_network_envelope(_envelope(7), from_http=True, source_index=0)

        # (1) Same-generation HTTP re-delivery (cold-ETag poll / fallback-poller
        # engage fetch) — dropped, NOT counted.
        assert not client._install_network_envelope(_envelope(7), from_http=True, source_index=0)
        # (2) Same-generation SSE message (sendInitialConfig on reconnect) —
        # dropped, NOT counted.
        assert not client._install_network_envelope(_envelope(7), from_http=False)

        event = reporter._failover_collector.drain()
        same_gen_rejections = 0 if event is None else event.failover.guard_rejected
        assert same_gen_rejections == 0, (
            f"equal-generation re-delivery was counted as guardRejected: {same_gen_rejections}"
        )
        assert client.held_generation() == 7, "equal generation must not re-install"

        # (3) A STRICTLY older payload IS the thing worth alerting on.
        assert not client._install_network_envelope(_envelope(6), from_http=True, source_index=1)
        event = reporter._failover_collector.drain()
        assert event is not None and event.failover is not None
        assert event.failover.guard_rejected == 1, (
            "a strictly older payload must still count as guardRejected"
        )
        assert client.held_generation() == 7
    finally:
        client.close()


def test_unversioned_carve_out_is_installed_not_counted() -> None:
    """An UNVERSIONED snapshot (generation <= 0) carries no ordering info, so
    the guard never rejects it — it is installed, and there is nothing to
    count. Guards the gen<=0 carve-out against the qfg-rr5b change."""
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=["http://127.0.0.1:1", "http://127.0.0.1:2"],
        collect_evaluation_summaries=False,
        context_upload_mode="shapes_only",
        fallback_poll_enabled=False,
    )
    try:
        reporter = client._telemetry
        assert reporter is not None
        assert client._install_network_envelope(_envelope(7), from_http=True, source_index=0)
        # generation 0 == unversioned: installed despite being "older".
        assert client._install_network_envelope(_envelope(0), from_http=True, source_index=0)
        event = reporter._failover_collector.drain()
        rejected = 0 if event is None else event.failover.guard_rejected
        assert rejected == 0, f"the unversioned carve-out counted a rejection: {rejected}"
    finally:
        client.close()


def test_steady_state_client_reports_zero_guard_rejected() -> None:
    """End-to-end: a healthy client talking to a server that never changes
    generation must report NO failover event at all (qfg-rr5b).

    This is the shape the bead measured. ``_load_from_api`` fires the initial
    hedged fetch AND engages the fallback poller, whose engage-time fetch (and
    every tick after it, the per-leg ETag slot being irrelevant here because the
    upstream always answers a full 200) returns the SAME generation. Each of
    those was counted, so a steady-state sdk-python client showed
    ``guardRejected: 1`` from init alone and climbing — a permanent false
    positive in the ``sdk_failover`` alerting signal.
    """
    upstream = _HedgeUpstream(generation=42)
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=[upstream.url],
        collect_evaluation_summaries=False,
        context_upload_mode="shapes_only",
        enable_sse=False,
        fallback_poll_enabled=True,
        fallback_poll_interval_ms=100,
        init_timeout_ms=8000,
        on_init_failure="return_zero_value",
    )
    try:
        client.init()
        reporter = client._telemetry
        assert reporter is not None
        # Let init's fetch, the poller's engage-time fetch and several ticks
        # land — every one of them a full 200 at generation 42.
        time.sleep(1.0)
        assert client.held_generation() == 42

        event = reporter._failover_collector.drain()
        rejected = 0 if event is None else event.failover.guard_rejected
        assert rejected == 0, (
            f"a steady-state client counted {rejected} guardRejected from "
            "same-generation re-delivery"
        )
    finally:
        client.close()
        upstream.close()
