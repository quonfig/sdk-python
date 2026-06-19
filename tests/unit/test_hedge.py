"""Parallel-failover hedge unit tests (qfg-7h5d.1.14).

Mirror sdk-go's quonfig_hedge_test.go. These pin the behaviors the chaos
ordering scenarios assert (o01 cold-standby, o03 heal-forward, o05
secondary-newer-wins) at the unit level, where a per-leg request counter can
prove the "secondary is never contacted on a fast primary" contract that the
chaos rig (no server-side counter) cannot.

RED baseline: on the pre-hedge sequential transport the primary is always tried
first and answers (within its per-URL timeout) for o05, so the client holds the
slow OLDER primary's 41 and the secondary is never contacted — RED. The hedge
makes a slow primary lose to the fast newer secondary (42) and heal forward —
GREEN. See the docstrings on each test for the exact failing/passing assertion.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from quonfig import Quonfig


def _envelope_json(generation: int) -> bytes:
    return json.dumps(
        {
            "configs": [],
            "meta": {
                "version": f"gen-{generation}",
                "environment": "Production",
                "generation": generation,
            },
        }
    ).encode()


class _HedgeUpstream:
    """An httptest-style server pinned to a Meta.generation, optionally delayed
    by ``delay_seconds`` before it answers, counting every request it receives."""

    def __init__(self, generation: int, delay_seconds: float = 0.0) -> None:
        self.generation = generation
        self.delay_seconds = delay_seconds
        self.hits = 0
        self._hits_lock = threading.Lock()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                with outer._hits_lock:
                    outer.hits += 1
                if outer.delay_seconds > 0:
                    time.sleep(outer.delay_seconds)
                body = _envelope_json(outer.generation)
                self.send_response(200)
                self.send_header("ETag", f'"gen-{outer.generation}"')
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:  # silence test server
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        host, port = self._server.server_address
        self.url = f"http://127.0.0.1:{port}"

    def hit_count(self) -> int:
        with self._hits_lock:
            return self.hits

    def close(self) -> None:
        self._server.shutdown()


def _new_hedge_client(primary_url: str, secondary_url: str) -> Quonfig:
    client = Quonfig(
        sdk_key="test-backend-key",
        api_urls=[primary_url, secondary_url],
        # SSE off + fallback poller off: isolate the INITIAL hedged fetch so the
        # heal-forward / reject-older behavior is attributable to the hedge, not
        # a background poll loop.
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        fallback_poll_enabled=False,
        init_timeout_ms=8000,
        on_init_failure="return_zero_value",
    )
    # SSE points at a dead port via the test seam so it never connects.
    if client._transport is not None:
        client._transport._Transport__test_stream_url_override = (  # type: ignore[attr-defined]
            "http://127.0.0.1:1/api/v2/sse/config"
        )
    return client


def _await_ready(client: Quonfig, within: float = 6.0) -> None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if client.ready():
            return
        time.sleep(0.02)


def _poll_until_generation(client: Quonfig, want: int, within: float) -> None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if client.held_generation() == want:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"held generation did not reach {want} within {within}s (last = {client.held_generation()})"
    )


def test_hedge_fast_primary_never_contacts_secondary() -> None:
    """Unit-level o01: both legs healthy and fast, secondary newer. A fast
    primary answers well inside the hedge delay, so the secondary is NEVER
    contacted (cold standby, zero extra load). The client holds the primary's
    (lower) generation, resolvedFrom stays 'primary', and installCount==1.

    RED (pre-hedge): the sequential transport also never contacts the secondary,
    so held==41 happens to pass — but this test is the cold-standby PROOF the
    hedge must preserve (secondary_hits == 0 even though the hedge exists)."""
    primary = _HedgeUpstream(generation=41, delay_seconds=0.0)
    secondary = _HedgeUpstream(generation=42, delay_seconds=0.0)
    client = _new_hedge_client(primary.url, secondary.url)
    try:
        client.init()
        _await_ready(client)

        assert client.held_generation() == 41, (
            f"held={client.held_generation()}, want 41 "
            "(fast primary wins; secondary's 42 must not be installed)"
        )
        assert client.resolved_from() == "primary"
        assert client.config_install_count() == 1
        # Hold window: the secondary must stay un-contacted.
        time.sleep(1.0)
        assert secondary.hit_count() == 0, (
            f"secondary contacted {secondary.hit_count()} times, want 0 "
            "(cold standby — a fast primary must never trigger the hedge)"
        )
        assert primary.hit_count() >= 1
        assert client.held_generation() == 41
        assert client.config_install_count() == 1
    finally:
        client.close()
        primary.close()
        secondary.close()


def test_hedge_secondary_newer_wins() -> None:
    """Unit-level o05 — the cleanest RED->GREEN discriminator: the primary is
    SLOW and serves the OLDER generation (41); the secondary is fast and serves
    the NEWER generation (42). The hedge fires the secondary once the hedge delay
    elapses (primary still slow), installs 42, and when the slow primary's older
    41 lands late the reject-older guard drops it.

    RED (pre-hedge sequential transport): the primary is tried first, answers
    (slowly, but inside the per-URL timeout) with 41, the secondary is never
    contacted, and the client holds 41. The hedge makes it hold 42 (GREEN)."""
    primary = _HedgeUpstream(generation=41, delay_seconds=2.5)
    secondary = _HedgeUpstream(generation=42, delay_seconds=0.0)
    client = _new_hedge_client(primary.url, secondary.url)
    try:
        client.init()
        _await_ready(client)

        # The hedge must have fired the secondary (slow primary) and installed 42.
        _poll_until_generation(client, 42, within=6.0)
        assert secondary.hit_count() >= 1, (
            "secondary was never contacted — the hedge did not fire against the slow primary"
        )

        # The slow primary's older 41 lands late and on every subsequent refresh;
        # the reject-older guard must keep the client on 42.
        for _ in range(3):
            client.refresh()
        assert client.held_generation() == 42, (
            f"held={client.held_generation()} after late older primary, want 42 "
            "(reject-older must drop the slow 41)"
        )
    finally:
        client.close()
        primary.close()
        secondary.close()


def test_hedge_heals_forward_to_slow_newer_primary() -> None:
    """Unit-level o03: the primary is SLOW and serves the NEWER generation (42);
    the secondary is fast and serves the OLDER generation (41). The hedge seeds
    readiness off the secondary's 41, then heals forward to the primary's 42 when
    it lands — reject-older only blocks going backward, never forward.

    RED (pre-hedge sequential transport): the secondary is never contacted (the
    slow primary answers first with 42), so secondary_hits == 0. The hedge
    contacts the secondary in parallel (GREEN)."""
    primary = _HedgeUpstream(generation=42, delay_seconds=2.5)
    secondary = _HedgeUpstream(generation=41, delay_seconds=0.0)
    client = _new_hedge_client(primary.url, secondary.url)
    try:
        client.init()
        _await_ready(client)

        assert secondary.hit_count() >= 1, (
            "secondary was never contacted — the hedge did not fire against the slow primary"
        )
        # Heal forward to the slow primary's newer 42.
        _poll_until_generation(client, 42, within=6.0)
    finally:
        client.close()
        primary.close()
        secondary.close()
