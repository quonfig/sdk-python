"""`enable_sse` constructor option (qfg-0xj3.1).

Mirrors sdk-node's `enableSSE` (src/quonfig.ts:283, 475-495). Three modes:

- ``enable_sse=True`` (default): SSE is the primary update channel and the
  Layer 2 fallback poller engages only on an SSE failure edge. Unchanged
  behavior — existing callers see exactly what they saw before.
- ``enable_sse=False`` + ``fallback_poll_enabled=True``: no SSE client is
  constructed at all and the fallback poller becomes the PRIMARY update
  channel — engaged immediately after the initial fetch is kicked off,
  because the SSE state edges that normally engage it will never arrive.
- ``enable_sse=False`` + ``fallback_poll_enabled=False``: initial fetch only;
  config moves solely via ``refresh()`` / ``update_if_staler_than()``.

RED baseline: ``enable_sse`` does not exist, so every constructor call below
raises ``TypeError: __init__() got an unexpected keyword argument
'enable_sse'``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator, List

from quonfig import Quonfig
from quonfig.transport import LegResult
from quonfig.types import ConfigEnvelope, Meta


def _envelope(generation: int) -> ConfigEnvelope:
    return ConfigEnvelope(
        configs=[],
        meta=Meta(version=f"gen-{generation}", environment="Production", generation=generation),
    )


class _FakeSSE:
    """Stand-in for ``quonfig.sse.SSEClient`` — records that it was built."""

    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True


class _SSERecorder:
    """Callable replacement for the SSEClient CLASS. Counts constructions so a
    test can prove SSE was never even built when ``enable_sse=False``."""

    def __init__(self) -> None:
        self.constructions = 0
        self.instances: List[_FakeSSE] = []

    def __call__(self, *args: Any, **kwargs: Any) -> _FakeSSE:
        self.constructions += 1
        inst = _FakeSSE()
        self.instances.append(inst)
        return inst


def _install_sse_recorder(monkeypatch: Any) -> _SSERecorder:
    recorder = _SSERecorder()
    monkeypatch.setattr("quonfig.sse.SSEClient", recorder)
    return recorder


def _make_client(**overrides: Any) -> Quonfig:
    kwargs: dict = dict(
        sdk_key="test-backend-key",
        api_urls=["http://127.0.0.1:1", "http://127.0.0.1:2"],
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        fallback_poll_enabled=True,
        fallback_poll_interval_ms=60000,
        init_timeout_ms=8000,
        on_init_failure="return_zero_value",
    )
    kwargs.update(overrides)
    return Quonfig(**kwargs)


def _stub_fetch(client: Quonfig, generation: int | None = None) -> dict:
    """Replace the transport's hedged fetch so no test touches the network.

    ``generation=None`` yields nothing (the fetch settles with no legs), which
    keeps the store empty; a generation yields one installable envelope.
    """
    calls = {"n": 0}
    assert client._transport is not None

    def fake_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        calls["n"] += 1
        if generation is None:
            return iter(())
        return iter([LegResult(source_index=0, envelope=_envelope(generation))])

    client._transport.fetch_hedged = fake_fetch_hedged  # type: ignore[method-assign]
    return calls


def _wait_for(predicate: Any, within: float = 3.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ----------------------------------------------------------------------
# Mode 1 — default (enable_sse=True): unchanged behavior
# ----------------------------------------------------------------------


def test_default_constructs_and_starts_sse(monkeypatch: Any) -> None:
    recorder = _install_sse_recorder(monkeypatch)
    client = _make_client()
    _stub_fetch(client)
    try:
        client.init()
        assert recorder.constructions == 1, "default mode must construct the SSE client"
        assert recorder.instances[0].started is True, "default mode must start SSE"
        assert client._enable_sse is True
    finally:
        client.close()


def test_default_does_not_engage_poller_without_sse_failure(monkeypatch: Any) -> None:
    """Layer 2 stays off while SSE is the primary channel — engagement is
    driven by SSE state edges, not by init."""
    _install_sse_recorder(monkeypatch)
    client = _make_client()
    _stub_fetch(client)
    try:
        client.init()
        time.sleep(0.1)
        assert client.fallback_poller_active() is False
    finally:
        client.close()


# ----------------------------------------------------------------------
# Mode 2 — enable_sse=False + fallback_poll_enabled=True: poll is PRIMARY
# ----------------------------------------------------------------------


def test_enable_sse_false_never_constructs_sse(monkeypatch: Any) -> None:
    recorder = _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False)
    _stub_fetch(client)
    try:
        client.init()
        time.sleep(0.1)
        assert recorder.constructions == 0, (
            "enable_sse=False must not construct the SSE client at all"
        )
        assert client._sse is None
    finally:
        client.close()


def test_poll_primary_engages_immediately(monkeypatch: Any) -> None:
    """With SSE off the poller is the only update channel, so it must engage at
    init — the SSE 'error' edge that normally engages it never arrives."""
    _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False)
    calls = _stub_fetch(client)
    try:
        client.init()
        assert _wait_for(lambda: client.fallback_poller_active()), (
            "poll-as-primary: the fallback poller must engage immediately at init"
        )
        # And it actually drives fetches (the poller fetches on engage).
        assert _wait_for(lambda: calls["n"] >= 1), "engaged poller never fetched"
    finally:
        client.close()


def test_poll_primary_engages_without_any_sse_state_edge(monkeypatch: Any) -> None:
    """Regression guard: engagement must NOT be gated on `_sse_ever_connected`
    / a state-change callback, which is dead code when SSE is disabled."""
    _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False)
    _stub_fetch(client)
    try:
        client.init()
        assert _wait_for(lambda: client.fallback_poller_active())
        assert client._last_sse_state is None, "no SSE state edge should ever be recorded"
    finally:
        client.close()


# ----------------------------------------------------------------------
# Mode 3 — enable_sse=False + fallback_poll_enabled=False: fetch once
# ----------------------------------------------------------------------


def test_no_sse_no_poll_is_initial_fetch_only(monkeypatch: Any) -> None:
    recorder = _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False, fallback_poll_enabled=False)
    calls = _stub_fetch(client, generation=42)
    try:
        client.init()
        assert _wait_for(lambda: client.held_generation() == 42), "initial fetch must still install"
        assert recorder.constructions == 0
        assert client._fallback_poller is None
        assert client.fallback_poller_active() is False
        time.sleep(0.2)
        assert calls["n"] == 1, f"config must move only on demand, saw {calls['n']} fetches"
        # refresh() is the only way forward.
        client.refresh()
        assert calls["n"] == 2
    finally:
        client.close()


# ----------------------------------------------------------------------
# connection_state() across the three modes
# ----------------------------------------------------------------------


def test_connection_state_poll_primary_is_connected_not_falling_back(monkeypatch: Any) -> None:
    """`falling_back` means degraded — SSE died and Layer 2 caught the client.
    Poll-as-primary is the configured design, not a degradation, so an
    installed poll-primary client reports `connected` (like datadir mode)."""
    _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False)
    _stub_fetch(client, generation=42)
    try:
        client.init()
        assert _wait_for(lambda: client.held_generation() == 42)
        assert _wait_for(lambda: client.fallback_poller_active())
        assert client.connection_state() == "connected", (
            "poll-as-primary is not a degraded state; it must not report falling_back"
        )
    finally:
        client.close()


def test_connection_state_sse_off_initializing_before_first_install(monkeypatch: Any) -> None:
    _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False, fallback_poll_enabled=False)
    _stub_fetch(client)  # settles with nothing installed
    try:
        assert client.connection_state() == "initializing"
        client.init()
        time.sleep(0.1)
        assert client.connection_state() == "initializing", (
            "no successful refresh yet — still initializing"
        )
    finally:
        client.close()


def test_connection_state_sse_off_connected_after_install(monkeypatch: Any) -> None:
    _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False, fallback_poll_enabled=False)
    _stub_fetch(client, generation=7)
    try:
        client.init()
        assert _wait_for(lambda: client.held_generation() == 7)
        assert client.connection_state() == "connected", (
            "SSE off behaves like datadir mode: connected once an envelope is installed"
        )
    finally:
        client.close()


def test_connection_state_default_mode_unchanged(monkeypatch: Any) -> None:
    """The SSE-on paths keep their existing semantics exactly."""
    _install_sse_recorder(monkeypatch)
    client = _make_client()
    _stub_fetch(client)
    try:
        client.init()
        client._handle_sse_state_change("connected")
        assert client.connection_state() == "connected"
        client._handle_sse_state_change("error")
        # Errored after a connect: grace timer pending, poller not yet engaged.
        assert client.connection_state() == "disconnected"
        assert client._fallback_poller is not None
        client._fallback_poller.engage("test")
        assert client.connection_state() == "falling_back"
    finally:
        client.close()


def test_boot_mode_logged(monkeypatch: Any, caplog: Any) -> None:
    """Each mode announces the chosen update channel at init (mirrors
    sdk-node's logBootMode)."""
    _install_sse_recorder(monkeypatch)

    cases = [
        (dict(), "SSE"),
        (dict(fallback_poll_enabled=False), "SSE"),
        (dict(enable_sse=False), "HTTP polling only"),
        (dict(enable_sse=False, fallback_poll_enabled=False), "NONE"),
    ]
    for overrides, expected in cases:
        with caplog.at_level("INFO", logger="quonfig.client"):
            caplog.clear()
            client = _make_client(**overrides)
            _stub_fetch(client)
            try:
                client.init()
                messages = [r.getMessage() for r in caplog.records]
                assert any("update channel" in m for m in messages), (
                    f"no update-channel boot log for {overrides}: {messages}"
                )
                assert any(expected in m for m in messages if "update channel" in m), (
                    f"boot log for {overrides} did not name {expected!r}: {messages}"
                )
            finally:
                client.close()


def test_sse_disabled_client_still_serves_and_refreshes(monkeypatch: Any) -> None:
    """End-to-end sanity: an SSE-off client initializes, serves, and heals
    forward through the poller without any SSE machinery."""
    _install_sse_recorder(monkeypatch)
    client = _make_client(enable_sse=False, fallback_poll_interval_ms=50)
    gen = {"v": 1}
    seen = threading.Event()
    assert client._transport is not None

    def fake_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        seen.set()
        return iter([LegResult(source_index=0, envelope=_envelope(gen["v"]))])

    client._transport.fetch_hedged = fake_fetch_hedged  # type: ignore[method-assign]
    try:
        client.init()
        assert _wait_for(lambda: client.held_generation() == 1)
        gen["v"] = 2
        assert _wait_for(lambda: client.held_generation() == 2, within=3.0), (
            "poll-primary client did not pick up the newer generation"
        )
        assert client.ready() is True
    finally:
        client.close()
