"""`update_if_staler_than(max_age_ms)` — non-blocking stale-while-revalidate
(qfg-0xj3.2).

Mirrors sdk-node's `updateIfStalerThan` (src/quonfig.ts:854) for serverless
hosts (Lambda / Vercel) where the background SSE + poll threads are frozen
between invocations, so the held config can be arbitrarily stale at the top of
a request. Contract:

- Fresh (``now - last_successful_refresh() <= max_age_ms``) → ``False``, zero
  work: one lock acquisition and a clock read, no network.
- Stale, or never refreshed → fire ONE hedged refresh cycle on a daemon thread
  and return ``True`` IMMEDIATELY. The caller never waits on the network;
  ``True`` means "a refresh was triggered", not "a refresh completed".
- Coalesced: while a staleness-triggered refresh is in flight, further calls
  are no-ops returning ``False``. This is called per-request, so a slow or
  down upstream must never stack threads — at most one is ever in flight.
- Worker-thread exceptions are caught and logged, never raised into the
  caller (which is long gone by then).
- No transport (datadir mode) → ``False``.

RED baseline: ``Quonfig.update_if_staler_than`` does not exist, so every test
below fails with ``AttributeError: 'Quonfig' object has no attribute
'update_if_staler_than'``.
"""

from __future__ import annotations

import datetime
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


def _make_client(**overrides: Any) -> Quonfig:
    """A transport-backed client that never touches the network: no ``init()``
    is called, so no SSE / initial fetch / poller ever starts."""
    kwargs: dict = dict(
        sdk_key="test-backend-key",
        api_urls=["http://127.0.0.1:1", "http://127.0.0.1:2"],
        collect_evaluation_summaries=False,
        context_upload_mode="none",
        fallback_poll_enabled=False,
        init_timeout_ms=8000,
        on_init_failure="return_zero_value",
    )
    kwargs.update(overrides)
    return Quonfig(**kwargs)


def _stamp_refresh(client: Quonfig, seconds_ago: float) -> None:
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    with client._health_lock:
        client._last_successful_refresh = stamp


def _wait_for(predicate: Any, within: float = 3.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_fresh_returns_false_and_does_no_work() -> None:
    client = _make_client()
    calls: List[int] = []
    assert client._transport is not None

    def fake_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        calls.append(1)
        return iter(())

    client._transport.fetch_hedged = fake_fetch_hedged  # type: ignore[method-assign]
    try:
        _stamp_refresh(client, seconds_ago=1.0)
        assert client.update_if_staler_than(60_000) is False
        time.sleep(0.1)
        assert calls == [], "a fresh client must not fetch"
    finally:
        client.close()


def test_stale_triggers_refresh_and_installs() -> None:
    client = _make_client()
    assert client._transport is not None

    def fake_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        return iter([LegResult(source_index=0, envelope=_envelope(42))])

    client._transport.fetch_hedged = fake_fetch_hedged  # type: ignore[method-assign]
    try:
        _stamp_refresh(client, seconds_ago=300.0)
        assert client.update_if_staler_than(60_000) is True
        assert _wait_for(lambda: client.held_generation() == 42), (
            "the triggered refresh never installed the new envelope"
        )
    finally:
        client.close()


def test_never_refreshed_triggers_refresh() -> None:
    """A cold client (no successful refresh yet) is maximally stale."""
    client = _make_client()
    fetched = threading.Event()
    assert client._transport is not None

    def fake_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        fetched.set()
        return iter([LegResult(source_index=0, envelope=_envelope(9))])

    client._transport.fetch_hedged = fake_fetch_hedged  # type: ignore[method-assign]
    try:
        assert client.last_successful_refresh() is None
        assert client.update_if_staler_than(60_000) is True
        assert fetched.wait(3.0), "never-refreshed client did not trigger a fetch"
    finally:
        client.close()


def test_returns_immediately_while_upstream_is_slow() -> None:
    """The caller is on a request path — it must never wait on the network."""
    client = _make_client()
    release = threading.Event()
    entered = threading.Event()
    assert client._transport is not None

    def slow_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        entered.set()
        release.wait(5.0)
        return iter(())

    client._transport.fetch_hedged = slow_fetch_hedged  # type: ignore[method-assign]
    try:
        started = time.monotonic()
        assert client.update_if_staler_than(0) is True
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, f"update_if_staler_than blocked for {elapsed:.2f}s — must not block"
        assert entered.wait(3.0), "the refresh never ran on its own thread"
    finally:
        release.set()
        client.close()


def test_coalesces_while_a_refresh_is_in_flight() -> None:
    """Called per-request against a down upstream, this must not stack threads:
    exactly ONE fetch in flight, every other call a cheap False."""
    client = _make_client()
    release = threading.Event()
    entered = threading.Event()
    calls: List[int] = []
    calls_lock = threading.Lock()
    assert client._transport is not None

    def slow_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        with calls_lock:
            calls.append(1)
        entered.set()
        release.wait(5.0)
        return iter(())

    client._transport.fetch_hedged = slow_fetch_hedged  # type: ignore[method-assign]
    try:
        assert client.update_if_staler_than(0) is True
        assert entered.wait(3.0)

        results = [client.update_if_staler_than(0) for _ in range(50)]
        assert results == [False] * 50, "in-flight refresh must coalesce subsequent calls"

        # Hammer it from several threads too — the flag must be lock-guarded.
        extra: List[bool] = []
        extra_lock = threading.Lock()

        def hammer() -> None:
            r = client.update_if_staler_than(0)
            with extra_lock:
                extra.append(r)

        threads = [threading.Thread(target=hammer) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)
        assert extra == [False] * 20

        with calls_lock:
            assert calls == [1], f"expected exactly one in-flight fetch, saw {len(calls)}"
    finally:
        release.set()
        client.close()


def test_in_flight_flag_clears_so_a_later_call_can_refresh() -> None:
    client = _make_client()
    calls: List[int] = []
    calls_lock = threading.Lock()
    assert client._transport is not None

    def fake_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        with calls_lock:
            calls.append(1)
        return iter(())

    client._transport.fetch_hedged = fake_fetch_hedged  # type: ignore[method-assign]
    try:
        assert client.update_if_staler_than(0) is True
        assert _wait_for(lambda: len(calls) == 1)
        # The worker's finally must clear the flag even though nothing installed.
        assert _wait_for(lambda: client.update_if_staler_than(0) is True), (
            "the in-flight flag was never cleared — a stuck flag wedges refreshes forever"
        )
        assert _wait_for(lambda: len(calls) == 2)
    finally:
        client.close()


def test_worker_exception_does_not_propagate_or_wedge() -> None:
    """A throwing transport must not raise into the caller, and must still
    clear the in-flight flag (the ``finally`` on the worker thread)."""
    client = _make_client()
    calls: List[int] = []
    calls_lock = threading.Lock()
    assert client._transport is not None

    def boom(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        with calls_lock:
            calls.append(1)
        raise RuntimeError("upstream on fire")

    client._transport.fetch_hedged = boom  # type: ignore[method-assign]
    try:
        assert client.update_if_staler_than(0) is True  # no exception here
        assert _wait_for(lambda: len(calls) == 1)
        assert _wait_for(lambda: client.update_if_staler_than(0) is True), (
            "a failed refresh must clear the in-flight flag"
        )
    finally:
        client.close()


def test_datadir_mode_is_a_noop(tmp_path: Any) -> None:
    """No transport to refresh from — nothing to do, never True."""
    client = Quonfig(datadir=str(tmp_path), environment="Production")
    try:
        assert client._transport is None
        assert client.update_if_staler_than(0) is False
    finally:
        client.close()


def test_boundary_equal_age_is_fresh() -> None:
    """`<= max_age_ms` is fresh: a very large window is never stale."""
    client = _make_client()
    calls: List[int] = []
    assert client._transport is not None

    def fake_fetch_hedged(*_a: Any, **_k: Any) -> Iterator[LegResult]:
        calls.append(1)
        return iter(())

    client._transport.fetch_hedged = fake_fetch_hedged  # type: ignore[method-assign]
    try:
        _stamp_refresh(client, seconds_ago=0.0)
        assert client.update_if_staler_than(10_000_000) is False
        time.sleep(0.05)
        assert calls == []
    finally:
        client.close()
