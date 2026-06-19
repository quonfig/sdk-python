from __future__ import annotations

import base64
import logging
import queue
import threading
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Callable, Iterator, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests  # type: ignore[import-untyped]

from .types import ConfigEnvelope

_LOG = logging.getLogger(__name__)


def derive_stream_url(api_url: str) -> str:
    """Derive the SSE stream base URL by prepending ``stream.`` to the hostname.

    Mirrors sdk-node's ``deriveStreamUrl`` and sdk-ruby's ``Options.derive_stream_url``:

        https://primary.quonfig.com       -> https://stream.primary.quonfig.com
        http://localhost:6550             -> http://stream.localhost:6550
        https://api.example.com/base      -> https://stream.api.example.com/base

    Scheme, port, and path are preserved.
    """
    parts = urlsplit(api_url)
    if not parts.hostname:
        return api_url
    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        userinfo += "@"
    host = f"stream.{parts.hostname}"
    netloc = f"{userinfo}{host}"
    if parts.port is not None:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


if TYPE_CHECKING:
    from .store import ConfigStore

try:
    QUONFIG_VERSION = _pkg_version("quonfig")
except PackageNotFoundError:
    QUONFIG_VERSION = "0.0.0-dev"


# Per-URL config-fetch deadline (qfg-7h5d.1.8). Bounds a single per-leg attempt
# — the initial fetch and every fallback-poller fetch alike — so a hung primary
# (accepts the connection but never responds) aborts fast and the secondary is
# reached inside the overall init budget instead of being starved until it. ~3s
# is short enough that a hung primary fails over well inside a default init
# budget, yet long enough to tolerate a slow-but-healthy upstream. It bounds the
# HTTP config path only — the long-lived SSE stream keeps its own read deadline.
DEFAULT_CONFIG_FETCH_TIMEOUT_SECONDS = 3.0


# Parallel-failover hedge timings (qfg-7h5d.1.14). Mirror sdk-go's
# DefaultConfigFetchHedgeDelay / DefaultConfigFetchHedgeAbort.
#
# HEDGE_DELAY is how long the hedge waits for the PRIMARY leg before ALSO firing
# the secondary in parallel (it does NOT cancel the primary). A healthy
# sub-second primary answers well inside the delay, so the secondary stays a cold
# standby and a healthy system adds zero secondary load. ~1s is below a realistic
# slow-but-alive primary's worst case yet far enough below the per-leg abort.
DEFAULT_CONFIG_FETCH_HEDGE_DELAY_SECONDS = 1.0

# HEDGE_ABORT is the per-leg hard-abort deadline on the hedged path. It MUST
# exceed the longest healable primary latency (the corpus o03/o05 use a 3s slow
# primary) so a late-but-newer primary heals forward rather than aborting, and
# MUST be < init_timeout so the init-path heal leg is not clipped.
DEFAULT_CONFIG_FETCH_HEDGE_ABORT_SECONDS = 6.0


@dataclass
class LegResult:
    """One hedged leg's outcome. Exactly one LegResult is emitted per fired leg;
    ``source_index`` identifies the leg (0 = primary, 1 = secondary, ...).

    Mirrors sdk-go's ``legResult``. ``envelope`` is the decoded config on a 200,
    ``None`` with ``not_changed=True`` on a 304, and ``error`` is set when the
    leg failed (connection error, timeout, non-2xx).
    """

    source_index: int
    envelope: Optional[ConfigEnvelope] = None
    not_changed: bool = False
    error: Optional[Exception] = None


class Transport:
    def __init__(
        self,
        api_urls: List[str],
        sdk_key: str,
        timeout: float = DEFAULT_CONFIG_FETCH_TIMEOUT_SECONDS,
        hedge_delay: float = DEFAULT_CONFIG_FETCH_HEDGE_DELAY_SECONDS,
        hedge_abort: float = DEFAULT_CONFIG_FETCH_HEDGE_ABORT_SECONDS,
    ) -> None:
        self.api_urls = api_urls
        self.sdk_key = sdk_key
        # Per-URL config-fetch deadline. Applied to every per-leg request so a
        # hung leg aborts after this duration and failover continues. Governs the
        # SEQUENTIAL ``fetch`` path; the hedged path uses ``hedge_abort`` instead.
        self.timeout = timeout
        # Parallel-failover hedge timings (qfg-7h5d.1.14). ``hedge_delay`` is how
        # long the hedge waits for the primary before also firing the secondary;
        # ``hedge_abort`` is the per-leg hard deadline on the hedged path.
        self.hedge_delay = hedge_delay
        self.hedge_abort = hedge_abort
        self._current_url_idx = 0
        # Index of the leg that produced the most recent fetch result (200 or
        # 304); -1 before the first fetch. The client reads this to report
        # `resolved_from()` for the SEQUENTIAL path. The hedged path reports the
        # leg explicitly via LegResult.source_index instead (a shared scalar
        # cannot identify which of two concurrent legs produced a given result).
        self.last_fetch_index = -1
        # Per-leg ETag store (qfg-7h5d.1.14). The hedge runs both legs
        # concurrently, so a single shared ETag would (a) be a data race and
        # (b) let a 304 from one leg mask the other. Each leg snapshots its own
        # slot before the request (under _etag_lock) and writes the response
        # ETag back after — the network wait happens with no lock held. Keyed by
        # base URL so it survives a leg-index reshuffle.
        self._etags: dict[str, str] = {}
        self._etag_lock = threading.Lock()
        self._session = requests.Session()
        # Test seam: when set, SSE routes here instead of deriving the URL
        # from `api_urls`. Mirrors sdk-node's `__testStreamUrlOverride`.
        self.__test_stream_url_override: Optional[str] = None

    def _auth_header(self) -> str:
        credentials = base64.b64encode(f"1:{self.sdk_key}".encode()).decode()
        return f"Basic {credentials}"

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {
            "Authorization": self._auth_header(),
            "X-Quonfig-SDK-Version": f"python-{QUONFIG_VERSION}",
        }
        if extra:
            h.update(extra)
        return h

    def _current_url(self) -> str:
        return self.api_urls[self._current_url_idx % len(self.api_urls)]

    def _current_stream_url(self) -> str:
        # SSE is pinned to the primary leg by design — the live stream does NOT
        # fail over (failover is an HTTP-only property; chaos scenario f05).
        # Deriving from the primary URL keeps the stream from silently
        # repointing to the secondary after an HTTP config-fetch failover has
        # moved ``_current_url_idx``.
        return derive_stream_url(self.api_urls[0])

    def _failover(self) -> None:
        self._current_url_idx += 1

    def close(self) -> None:
        """Release the underlying ``requests.Session``'s connection pool."""
        self._session.close()

    def fetch(self, etag: Optional[str] = None) -> Optional[ConfigEnvelope]:
        """
        Fetch configs from API.

        Returns None on 304 (not modified).
        Raises RuntimeError if all URLs fail.
        """
        headers = self._headers()
        if etag:
            headers["If-None-Match"] = etag

        last_error: Optional[Exception] = None
        # Always start the failover loop at the primary leg (index 0) — the loop
        # is NOT sticky to the leg that last worked. This way a recovered primary
        # is preferred again on the next fetch, and a newer primary heals an
        # established client forward when it lands late (chaos scenario o03).
        # Each per-leg request is bounded by ``self.timeout`` so a hung primary
        # aborts fast and the secondary is reached inside the init budget (f02).
        for idx in range(len(self.api_urls)):
            self._current_url_idx = idx
            try:
                url = f"{self.api_urls[idx]}/api/v2/configs"
                response = self._session.get(url, headers=headers, timeout=self.timeout)
                if response.status_code == 304:
                    self.last_fetch_index = idx
                    return None
                response.raise_for_status()
                envelope = ConfigEnvelope.from_dict(response.json())
                self.last_fetch_index = idx
                return envelope
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                last_error = e
                continue
        raise RuntimeError(f"All API URLs failed: {last_error}")

    def _fetch_leg(self, idx: int, abort: float) -> LegResult:
        """Fetch GET /api/v2/configs from ``api_urls[idx]`` using ONLY that leg's
        ETag slot, bounded by its own abort deadline. Fully reads/decodes the body
        before returning. Returns a LegResult tagged with ``source_index=idx``.

        Mirrors sdk-go's ``fetchFromURLAt``. Network errors, timeouts, and non-2xx
        responses are captured in ``LegResult.error`` rather than raised so the
        hedge orchestrator can drain every fired leg's outcome uniformly.
        """
        if idx < 0 or idx >= len(self.api_urls):
            return LegResult(source_index=idx, error=IndexError(f"leg index {idx} out of range"))
        base_url = self.api_urls[idx]

        headers = self._headers()
        # Snapshot this leg's ETag under the lock, then release before the network
        # wait. A 304 from one leg cannot mask the other because each leg conditions
        # on its OWN last-seen ETag.
        with self._etag_lock:
            etag = self._etags.get(base_url)
        if etag:
            headers["If-None-Match"] = etag

        try:
            url = f"{base_url}/api/v2/configs"
            response = self._session.get(url, headers=headers, timeout=abort)
            if response.status_code == 304:
                self.last_fetch_index = idx
                return LegResult(source_index=idx, not_changed=True)
            response.raise_for_status()
            new_etag = response.headers.get("ETag")
            if new_etag:
                with self._etag_lock:
                    self._etags[base_url] = new_etag
            envelope = ConfigEnvelope.from_dict(response.json())
            self.last_fetch_index = idx
            return LegResult(source_index=idx, envelope=envelope)
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            return LegResult(source_index=idx, error=e)

    def fetch_hedged(
        self,
        hedge_delay: Optional[float] = None,
        hedge_abort: Optional[float] = None,
    ) -> Iterator[LegResult]:
        """Fire the PRIMARY leg (index 0) and, if it has not settled within
        ``hedge_delay`` OR errors fast, ALSO fire the secondary leg (index 1) in
        parallel — without cancelling the primary. Both legs run under their own
        ``hedge_abort`` deadline and their own ETag slot.

        Yields one LegResult per FIRED leg, in arrival order; the generator
        finishes once every fired leg has settled, so the number yielded equals
        the number fired. A fast healthy primary means the secondary is NEVER
        contacted (cold standby, zero extra load on a healthy system).

        Mirrors sdk-go's ``FetchConfigsHedged``. The caller installs each
        successful result through the reject-older guard so watermark-max (higher
        generation wins; late older never regresses; late newer heals forward)
        falls out without any source ranking. Concurrency model: one daemon
        thread per fired leg, results funneled through a ``queue.Queue``.
        """
        delay = hedge_delay if hedge_delay is not None else self.hedge_delay
        abort = hedge_abort if hedge_abort is not None else self.hedge_abort
        if delay <= 0:
            delay = DEFAULT_CONFIG_FETCH_HEDGE_DELAY_SECONDS
        if abort <= 0:
            abort = DEFAULT_CONFIG_FETCH_HEDGE_ABORT_SECONDS

        has_secondary = len(self.api_urls) > 1
        out: "queue.Queue[LegResult]" = queue.Queue()
        # Mirror of every result so the arbiter can inspect the primary's outcome
        # without consuming it from ``out`` (the caller drains ``out``).
        prim_q: "queue.Queue[LegResult]" = queue.Queue(maxsize=1)

        # CAS-style guard so the secondary fires AT MOST ONCE and NEVER after a
        # fast primary win. ``threading.Lock`` + a bool gives us the
        # compare-and-set. ``fired`` counts the legs actually started under the
        # same lock so the drain below reads exactly that many results.
        fire_lock = threading.Lock()
        secondary_decided = False  # True once we've committed to fire-or-suppress
        fired = 1  # primary always fires

        def _run_leg(idx: int, mirror_primary: bool) -> None:
            lr = self._fetch_leg(idx, abort)
            if mirror_primary:
                try:
                    prim_q.put_nowait(lr)
                except queue.Full:
                    pass
            out.put(lr)

        def _fire_secondary() -> None:
            nonlocal secondary_decided, fired
            if not has_secondary:
                return
            with fire_lock:
                if secondary_decided:
                    return
                secondary_decided = True
                fired = 2
            threading.Thread(
                target=_run_leg, args=(1, False), daemon=True, name="quonfig-hedge-secondary"
            ).start()

        def _suppress_secondary() -> None:
            # Fast primary win: commit to never firing the secondary.
            nonlocal secondary_decided
            with fire_lock:
                secondary_decided = True

        # Fire the primary.
        threading.Thread(
            target=_run_leg, args=(0, True), daemon=True, name="quonfig-hedge-primary"
        ).start()

        # Arbiter: wait up to ``delay`` for the primary. If it errors fast, hedge
        # now; if it succeeds/304s fast, suppress the secondary; if the delay
        # elapses with the primary still in flight, hedge in parallel.
        try:
            lr = prim_q.get(timeout=delay)
            if lr.error is not None:
                _fire_secondary()  # fast error -> hedge now
            else:
                _suppress_secondary()  # fast success/304 -> never hedge
        except queue.Empty:
            # Delay elapsed. Re-check the primary so a primary that JUST won the
            # boundary race does not trigger an unnecessary hedge.
            try:
                lr = prim_q.get_nowait()
                if lr.error is not None:
                    _fire_secondary()
                else:
                    _suppress_secondary()
            except queue.Empty:
                _fire_secondary()  # primary still in flight -> hedge in parallel

        # Snapshot how many legs were actually started under the lock.
        with fire_lock:
            total = fired

        # Drain exactly ``total`` settled legs in arrival order. ``_fetch_leg`` is
        # bounded by ``abort`` and always puts exactly one result, so this never
        # blocks indefinitely.
        for _ in range(total):
            yield out.get()


class FallbackPoller:
    """Layer 2 HTTP fallback poller.

    Off by default — only engages when SSE is unavailable (initial-connect
    failure or sustained disconnect). Replaces the previous always-on parallel
    poll which doubled bandwidth and had no reconcile logic. Mirrors sdk-node's
    ``startFallbackPolling`` / ``engageFallbackPoller`` /
    ``disengageFallbackPoller`` triplet so cross-SDK chaos scenarios behave
    identically (qfg-47c2.8).
    """

    def __init__(
        self,
        transport: Transport,
        store: "ConfigStore",
        interval_seconds: float,
        shutdown_event: threading.Event,
        on_config_update: Optional[Callable[[], None]] = None,
        install: Optional[Callable[["ConfigEnvelope"], bool]] = None,
        refresh: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._transport = transport
        self._store = store
        self._interval = interval_seconds
        self._shutdown = shutdown_event
        self._on_config_update = on_config_update
        # Guarded install hook (qfg-7h5d.1.8). A poll fetch is installed through
        # the client's reject-older guard, which returns whether the install was
        # accepted; ``on_config_update`` fires only on an accepted install so a
        # failover to a stale (same-or-older) secondary can't regress or flap an
        # established client. Falls back to an unconditional store update when no
        # hook is wired (legacy callers).
        self._install = install
        # Hedged-refresh hook (qfg-7h5d.1.14). When wired, each poll tick drives a
        # full parallel-failover hedge cycle (primary first, secondary in parallel
        # only on slow/error) instead of the sequential primary-first fetch — so a
        # heal-forward to a newer leg happens on the poll loop, not just at init.
        # The callable installs through the reject-older guard and fires
        # on_config_update itself, returning whether anything was installed.
        # Supersedes the sequential fetch+install path when present.
        self._refresh = refresh
        self._lock = threading.Lock()
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def is_active(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def engage(self, reason: str) -> bool:
        """Start polling. No-op (returns False) if already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._loop,
                args=(stop_event,),
                daemon=True,
                name="quonfig-fallback-poll",
            )
            self._stop_event = stop_event
            self._thread = thread
        _LOG.warning(
            "[quonfig] SSE unavailable (%s); engaging HTTP fallback poll every %sms",
            reason,
            int(self._interval * 1000),
        )
        thread.start()
        return True

    def disengage(self, reason: str) -> bool:
        """Stop polling. No-op (returns False) if not running."""
        with self._lock:
            stop_event = self._stop_event
            self._stop_event = None
            self._thread = None
        if stop_event is None:
            return False
        stop_event.set()
        _LOG.info("[quonfig] HTTP fallback poll disengaged (%s)", reason)
        return True

    def _loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set() and not self._shutdown.is_set():
            # Wait the interval first; matches the long-polling cadence (the
            # initial config snapshot already came from `Transport.fetch` at
            # init-time) and lets disengage() short-circuit a sleeping cycle.
            if stop_event.wait(self._interval):
                return
            if self._shutdown.is_set():
                return
            try:
                if self._refresh is not None:
                    # Hedged path (qfg-7h5d.1.14): the refresh callable installs
                    # through the reject-older guard and fires on_config_update
                    # itself, so there is nothing more to do here.
                    self._refresh()
                    continue
                etag = self._store.get_etag()
                envelope = self._transport.fetch(etag=etag)
                if envelope is not None:
                    if self._install is not None:
                        installed = self._install(envelope)
                    else:
                        installed = self._store.update(envelope)
                    # Reject-older guard dropped a same-or-older payload — the
                    # held config is unchanged, so skip the update callback.
                    if installed and self._on_config_update is not None:
                        try:
                            self._on_config_update()
                        except Exception as e:  # noqa: BLE001
                            _LOG.error(
                                "Quonfig fallback poll: onConfigUpdate threw: %s: %s",
                                type(e).__name__,
                                e,
                            )
            except Exception as e:  # noqa: BLE001 — fallback errors must not kill the loop
                _LOG.debug(
                    "Quonfig fallback poll iteration failed: %s: %s",
                    type(e).__name__,
                    e,
                )
