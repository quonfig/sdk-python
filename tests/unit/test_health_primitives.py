"""Customer-visible health primitives (qfg-47c2.15).

Covers the two diagnostic getters required by Tier 1 unit-test 6 of
``project/plans/sdk-hardening-and-verification.md``:

- ``client.last_successful_refresh()`` — wall-clock time of the most recent
  installed envelope.
- ``client.connection_state()`` — one of ``connected``/``disconnected``/
  ``falling_back``/``initializing``.

NO ``healthy()`` is exposed; the plan explicitly forbids a binary primitive
because customers will wire it into k8s liveness probes.
"""

from __future__ import annotations

import datetime
import threading
import time

from quonfig import Quonfig


def _make_client(**overrides: object) -> Quonfig:
    kwargs: dict = dict(
        sdk_key="sdk-test",
        api_urls=["http://localhost:0"],
        fallback_poll_enabled=True,
        fallback_poll_interval_ms=60000,
    )
    kwargs.update(overrides)
    return Quonfig(**kwargs)  # type: ignore[arg-type]


def test_no_healthy_primitive() -> None:
    """The plan explicitly forbids a `healthy()` boolean — customers would
    wire it into k8s liveness probes and amplify blips into restart cascades."""
    client = _make_client()
    try:
        assert not hasattr(client, "healthy"), (
            "Quonfig must NOT expose a healthy() primitive — see "
            "sdk-hardening-and-verification.md Phase 4."
        )
    finally:
        client.close()


def test_last_successful_refresh_none_before_install() -> None:
    client = _make_client()
    try:
        assert client.last_successful_refresh() is None
    finally:
        client.close()


def test_connection_state_initializing_before_first_install() -> None:
    client = _make_client()
    try:
        assert client.connection_state() == "initializing"
    finally:
        client.close()


def test_connection_state_connected_after_sse_connect() -> None:
    client = _make_client()
    try:
        client._handle_sse_state_change("connected")
        assert client.connection_state() == "connected"
    finally:
        client.close()


def test_connection_state_disconnected_after_sse_error_post_connect() -> None:
    """After a successful connect, an `error` edge transitions to
    `disconnected` — until the grace timer engages the fallback poller."""
    client = _make_client(fallback_poll_interval_ms=60000)
    try:
        client._handle_sse_state_change("connected")
        client._handle_sse_state_change("error")
        # Grace timer is pending, fallback not yet active.
        assert client.connection_state() == "disconnected"
    finally:
        client.close()


def test_connection_state_falling_back_when_poller_engages() -> None:
    """Initial-SSE-failure engages the fallback poller immediately —
    state must reflect `falling_back`, not `disconnected`."""
    client = _make_client()
    try:
        client._handle_sse_state_change("error")
        assert client.fallback_poller_active() is True
        assert client.connection_state() == "falling_back"
    finally:
        client.close()


def test_connection_state_connected_after_sse_recovers() -> None:
    """Recovery: falling_back → connected once SSE reconnects."""
    client = _make_client()
    try:
        client._handle_sse_state_change("error")
        assert client.connection_state() == "falling_back"
        client._handle_sse_state_change("connected")
        assert client.connection_state() == "connected"
    finally:
        client.close()


def test_last_successful_refresh_stamps_on_datadir_init(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A successful datadir install must populate last_successful_refresh().

    The point: every install path funnels through `_fire_on_config_update`,
    so the datadir code path stamps the refresh time the same way SSE and
    fallback poll do.
    """
    from quonfig.types import ConfigEnvelope, Meta

    def fake_load_datadir(datadir: str, environment: str) -> ConfigEnvelope:
        return ConfigEnvelope(configs=[], meta=Meta(version="test", environment=environment))

    monkeypatch.setattr("quonfig.datadir.load_datadir", fake_load_datadir)

    before = datetime.datetime.now(datetime.timezone.utc)
    client = Quonfig(datadir="/fake", environment="production")
    try:
        client.init()
        stamp = client.last_successful_refresh()
        after = datetime.datetime.now(datetime.timezone.utc)
        assert stamp is not None
        assert before <= stamp <= after
    finally:
        client.close()


def test_last_successful_refresh_advances_on_subsequent_install() -> None:
    """Each install (SSE event or fallback poll) bumps the stamp forward."""
    client = _make_client()
    try:
        client._fire_on_config_update()
        first = client.last_successful_refresh()
        assert first is not None
        # Sleep just enough for monotonic clock to advance.
        time.sleep(0.01)
        client._fire_on_config_update()
        second = client.last_successful_refresh()
        assert second is not None
        assert second > first
    finally:
        client.close()


def test_connection_state_transitions_full_cycle() -> None:
    """Tier 1 unit-test 6: transitions through documented values during
    connect → error → reconnect."""
    client = _make_client()
    try:
        states: list[str] = []
        states.append(client.connection_state())  # initializing

        client._handle_sse_state_change("connecting")
        states.append(client.connection_state())  # still initializing (no install yet)

        client._handle_sse_state_change("connected")
        states.append(client.connection_state())  # connected

        client._handle_sse_state_change("error")
        states.append(client.connection_state())  # disconnected (grace pending)

        client._handle_sse_state_change("connected")
        states.append(client.connection_state())  # connected

        assert states[0] == "initializing"
        assert states[2] == "connected"
        assert states[3] == "disconnected"
        assert states[4] == "connected"
    finally:
        client.close()


def test_last_successful_refresh_is_thread_safe() -> None:
    """Concurrent installs must not corrupt the stamp."""
    client = _make_client()
    try:

        def hammer() -> None:
            for _ in range(100):
                client._fire_on_config_update()

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert client.last_successful_refresh() is not None
    finally:
        client.close()
