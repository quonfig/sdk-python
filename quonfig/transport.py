from __future__ import annotations

import base64
import json
import logging
import queue
import random
import threading
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Callable, Iterator, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests  # type: ignore[import-untyped]
import urllib3.exceptions

from .types import ConfigEnvelope, InvalidEnvelopeError

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
# standby and a healthy system adds zero secondary load. ~2s is below a realistic
# slow-but-alive primary's worst case yet far enough below the per-leg abort.
DEFAULT_CONFIG_FETCH_HEDGE_DELAY_SECONDS = 2.0

# HEDGE_ABORT is the per-leg hard-abort deadline on the hedged path. It MUST
# exceed the longest healable primary latency (the corpus o03/o05 use a 3s slow
# primary) so a late-but-newer primary heals forward rather than aborting, and
# MUST be < init_timeout so the init-path heal leg is not clipped.
DEFAULT_CONFIG_FETCH_HEDGE_ABORT_SECONDS = 6.0


# Size cap for a 200 body from /api/v2/configs (supervisor contract "Rule —
# size caps", qfg-6vno.8). 4x the SSE event cap, so an envelope too big for SSE
# still stays fresh through the fallback poller. A constant, not an option.
MAX_CONFIG_BODY_BYTES = 16 * 1024 * 1024


class ConfigBodyTooLargeError(Exception):
    """A 200 config body over MAX_CONFIG_BODY_BYTES: a failed leg, exactly like
    a 5xx (size-caps rule). Nothing is installed and no ETag is stored."""

    def __init__(self) -> None:
        super().__init__(f"config body exceeds the maximum size ({MAX_CONFIG_BODY_BYTES} bytes)")


def _redact_source(url: str) -> str:
    """``scheme://host[:port]`` of ``url``: no userinfo, path or query, for
    log lines that name a source."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        return f"{parts.scheme}://{host}" if parts.scheme else host
    except ValueError:
        return "?"


def _read_body_within_deadline(
    response: "requests.Response",
    deadline: float,
    max_bytes: int = MAX_CONFIG_BODY_BYTES,
) -> bytes:
    """Drain a ``stream=True`` response body under a WALL-CLOCK deadline
    (``time.monotonic()`` seconds), raising ``requests.Timeout`` once it passes.

    Why this exists (qfg-41nh.10): a scalar ``requests`` timeout only bounds
    connect + BETWEEN-BYTES gaps. A slow-drip upstream (one byte before every
    timeout tick, body never finishes) resets the read timer forever, so the
    "per-leg hard abort" was not actually wall-clock and a dripping leg wedged
    the fallback-poll loop while SSE was already down.

    Size cap (size-caps rule, qfg-6vno.8): a ``Content-Length`` over
    ``max_bytes`` fails before reading; otherwise bytes are counted as they
    arrive and reading stops at ``max_bytes + 1``. Either way it raises
    :class:`ConfigBodyTooLargeError`, so memory stays bounded near the cap.

    Reads at most one raw socket recv at a time so the deadline is checked as
    bytes arrive, never after a full chunk accumulates:

    - urllib3 v2 exposes ``HTTPResponse.read1`` (returns whatever a single recv
      produced, up to ``amt``) — the fast path.
    - urllib3 1.x has no ``read1``; fall back to ``iter_content(chunk_size=1)``,
      where each next() is satisfied by ONE buffered recv (correct, slower —
      compatibility only).

    Worst case is bounded by deadline + one between-bytes read timeout (the
    recv in flight when the deadline passes), i.e. ~2x the per-leg budget —
    versus unbounded before.
    """
    declared = getattr(response, "headers", {}).get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise ConfigBodyTooLargeError()
        except ValueError:
            pass
    buf = bytearray()
    raw = getattr(response, "raw", None)
    read1 = getattr(raw, "read1", None)
    if callable(read1):
        while True:
            if time.monotonic() >= deadline:
                raise requests.Timeout("per-leg wall-clock deadline exceeded reading body")
            piece = read1(min(16384, max_bytes + 1 - len(buf)), decode_content=True)
            if not piece:
                return bytes(buf)
            buf += piece
            if len(buf) > max_bytes:
                raise ConfigBodyTooLargeError()
    for piece in response.iter_content(chunk_size=1):
        if time.monotonic() >= deadline:
            raise requests.Timeout("per-leg wall-clock deadline exceeded reading body")
        buf += piece
        if len(buf) > max_bytes:
            raise ConfigBodyTooLargeError()
    return bytes(buf)


def _decode_envelope(body: bytes) -> ConfigEnvelope:
    """Decode + validate an HTTP config body. Malformed JSON and non-envelope
    payloads both raise :class:`InvalidEnvelopeError` so callers treat them as a
    leg error (qfg-9dxb.3)."""
    try:
        data = json.loads(body)
    except ValueError as e:
        raise InvalidEnvelopeError(f"config payload is not valid JSON: {e}") from e
    return ConfigEnvelope.from_wire(data)


# Auth-failure rule (qfg-goi1.4, plan project/plans/2026-10-07-sdk-auth-failure-rule.md
# R1-R6; ITD chaos/supervisor-test-contract.md Test 8). R1: these statuses on a
# config fetch leg are the auth class. Every other status keeps its transient
# handling.
AUTH_CLASS_STATUSES = frozenset({401, 403, 404, 410})

# R3: while auth-failed, one probe cycle per period T, drawn uniform in
# [min, max] each time. No doubling, no give-up. Internal only, never a
# customer option.
AUTH_PROBE_MIN_SECONDS = 150.0
AUTH_PROBE_MAX_SECONDS = 300.0


def is_auth_class_status(status: int) -> bool:
    return status in AUTH_CLASS_STATUSES


def is_timeout_error(error: Optional[BaseException]) -> bool:
    """A leg error that is a hang: connect/read timeout, the per-leg
    wall-clock abort, or urllib3's own ``ReadTimeoutError`` from a body that
    stalls mid-read (``raw.read1`` is not wrapped by requests)."""
    return isinstance(error, (requests.Timeout, TimeoutError, urllib3.exceptions.TimeoutError))


@dataclass
class CycleOutcome:
    """What one hedged config-fetch cycle produced, as the auth-failure rule
    reads it.

    ``answered`` is True when any leg returned a 304 or a 200 with a valid
    envelope (installed or dropped by the reject-older guard). ``auth_status``
    is the primary leg's status when the cycle is a rejecting one (R2: no leg
    answered and the primary returned an auth-class status), else 0;
    ``auth_host`` is then the primary's host[:port], never the key.
    """

    installed: bool = False
    answered: bool = False
    auth_status: int = 0
    auth_host: str = ""


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
    # HTTP status the leg answered with, or 0 when no response came back
    # (connection error, timeout). The auth-failure rule reads the primary
    # leg's status (qfg-goi1.10.25).
    status: int = 0


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
        # Legs in an oversized-body episode (size-caps rule): one WARN per
        # episode, ended by the next valid envelope from that leg. Guarded by
        # ``_etag_lock``.
        self._oversized_sources: set[str] = set()
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

    def forget_etag(self, idx: int) -> None:
        """Drop leg ``idx``'s remembered ETag so its next request is a full GET.

        Used when a 200 was fetched but deliberately not installed (a gen<=0
        payload ignored over a held generation, qfg-9dxb.9, or one dropped as
        strictly older, qfg-j7ui): the server can
        later repair the generation for the SAME sha, so keeping that ETag
        would 304 the fix away until the next commit.
        """
        if 0 <= idx < len(self.api_urls):
            with self._etag_lock:
                self._etags.pop(self.api_urls[idx], None)

    def _read_config_body(
        self, response: "requests.Response", deadline: float, base_url: str
    ) -> bytes:
        """Read a 200 config body under the leg deadline and the size cap. An
        oversized body logs one WARN per episode for its source (repeats at
        DEBUG) and re-raises, failing the leg like a 5xx."""
        try:
            return _read_body_within_deadline(response, deadline)
        except ConfigBodyTooLargeError:
            with self._etag_lock:
                first = base_url not in self._oversized_sources
                self._oversized_sources.add(base_url)
            log = _LOG.warning if first else _LOG.debug
            log(
                "Quonfig: config body from %s exceeds the maximum size (%d bytes); "
                "the fetch failed",
                _redact_source(base_url),
                MAX_CONFIG_BODY_BYTES,
            )
            raise

    def _end_oversized_episode(self, base_url: str) -> None:
        with self._etag_lock:
            self._oversized_sources.discard(base_url)

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
        # Each per-leg request is bounded by ``self.timeout`` as a true
        # WALL-CLOCK deadline — connect + headers + full body read — so a hung
        # OR slow-drip primary aborts fast and the secondary is reached inside
        # the init budget (f02). The scalar requests timeout alone only bounds
        # between-bytes gaps, which a drip resets forever (qfg-41nh.10).
        for idx in range(len(self.api_urls)):
            self._current_url_idx = idx
            try:
                url = f"{self.api_urls[idx]}/api/v2/configs"
                deadline = time.monotonic() + self.timeout
                response = self._session.get(
                    url, headers=headers, timeout=self.timeout, stream=True
                )
                try:
                    if response.status_code == 304:
                        self.last_fetch_index = idx
                        return None
                    response.raise_for_status()
                    body = self._read_config_body(response, deadline, self.api_urls[idx])
                finally:
                    response.close()
                # A non-envelope 200 (proxy/WAF junk) is a leg error, so the
                # loop fails over instead of installing it (qfg-9dxb.3).
                envelope = _decode_envelope(body)
                self._end_oversized_episode(self.api_urls[idx])
                self.last_fetch_index = idx
                return envelope
            except (
                requests.RequestException,
                # The body is drained via ``raw.read1``, which raises urllib3
                # errors (truncated/reset body -> ProtocolError, stalled body ->
                # ReadTimeoutError) that requests does not wrap on that path.
                # They are leg errors like any other: fail over (qfg-goi1.2.13).
                urllib3.exceptions.HTTPError,
                OSError,
                InvalidEnvelopeError,
                ConfigBodyTooLargeError,
            ) as e:
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

        status = 0
        try:
            url = f"{base_url}/api/v2/configs"
            # ``abort`` is a WALL-CLOCK deadline over the whole leg — connect +
            # headers + full body read. The scalar requests timeout below only
            # bounds connect/between-bytes gaps (a slow-drip upstream resets it
            # forever), so the body is drained under an explicit monotonic
            # deadline (qfg-41nh.10).
            deadline = time.monotonic() + abort
            response = self._session.get(url, headers=headers, timeout=abort, stream=True)
            status = response.status_code
            try:
                if response.status_code == 304:
                    self.last_fetch_index = idx
                    return LegResult(source_index=idx, not_changed=True, status=status)
                response.raise_for_status()
                new_etag = response.headers.get("ETag")
                body = self._read_config_body(response, deadline, base_url)
            finally:
                response.close()
            # Validate BEFORE recording the ETag: a non-envelope 200 is a leg
            # error (so the hedge fires the other leg) and its ETag must never be
            # stored — otherwise later 304s would pin the client to a payload it
            # never installed (qfg-9dxb.3).
            envelope = _decode_envelope(body)
            # Store the leg's ETag only AFTER the body fully arrived and
            # validated — recording it earlier would make the next conditional
            # request 304 against a payload this client never installed.
            if new_etag:
                with self._etag_lock:
                    self._etags[base_url] = new_etag
            self._end_oversized_episode(base_url)
            self.last_fetch_index = idx
            return LegResult(source_index=idx, envelope=envelope, status=status)
        except Exception as e:  # noqa: BLE001
            # Every failure of a leg is a leg error, including urllib3 errors
            # raised mid-body by ``raw.read1`` (truncated/reset body ->
            # ProtocolError, stalled body -> ReadTimeoutError) that requests does
            # not wrap. Letting one escape killed the leg thread before it put a
            # result, so the hedge waited out its whole drain budget
            # (qfg-goi1.2.13).
            return LegResult(source_index=idx, error=e, status=status)

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
            # Always settle the leg: the drain below waits for exactly one
            # result per fired leg, so a leg that dies without putting one
            # stalls the hedge for its whole drain budget (qfg-goi1.2.13).
            lr: Optional[LegResult] = None
            try:
                lr = self._fetch_leg(idx, abort)
            except Exception as e:  # noqa: BLE001
                lr = LegResult(source_index=idx, error=e)
            finally:
                if lr is None:  # a BaseException is propagating
                    lr = LegResult(source_index=idx, error=RuntimeError("hedge leg aborted"))
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

        # Drain exactly ``total`` settled legs in arrival order. ``_fetch_leg``
        # self-aborts at its wall-clock deadline, but the queue read is ALSO
        # bounded (qfg-41nh.10): this generator is driven by the fallback-poll
        # loop, and an untimed get() would wedge BOTH refresh layers if a leg
        # ever outlived its deadline. Budget: the secondary fires up to ``delay``
        # after start, and each leg's true worst case is ~2x ``abort`` (deadline
        # elapse + one last between-bytes read), plus scheduling grace.
        drain_deadline = time.monotonic() + delay + (2 * abort) + 1.0
        for drained in range(total):
            remaining = drain_deadline - time.monotonic()
            if remaining <= 0:
                remaining = 0.001  # deadline passed — poll, don't block
            try:
                yield out.get(timeout=remaining)
            except queue.Empty:
                _LOG.warning(
                    "[quonfig] hedge drain abandoned %d unsettled leg(s) after "
                    "the per-leg deadlines elapsed — continuing without them",
                    total - drained,
                )
                return


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
        wait_hook: Optional[Callable[[str, float], float]] = None,
        cycle: Optional[Callable[[], CycleOutcome]] = None,
        on_auth_failed: Optional[Callable[[int, str], None]] = None,
        on_auth_recovered: Optional[Callable[[], None]] = None,
        polling_mode: bool = False,
    ) -> None:
        self._transport = transport
        # Test seam (supervisor contract Test 8): maps each requested wait to
        # the duration actually waited. ``None`` in production.
        self._wait_hook = wait_hook
        # Auth-failure rule (qfg-goi1.10.25, R2-R6). ``cycle`` runs one hedged
        # fetch cycle and reports how the legs answered; it supersedes
        # ``refresh`` when wired. A cycle with no 200/304 on any leg and an
        # auth-class status on the primary puts the poller in the auth-failed
        # regime: ``on_auth_failed(status, host)`` stops SSE and logs the one
        # ERROR, the poller then sends one probe cycle per T in [150s, 300s]
        # (``max(T, interval)`` in ``polling_mode``) until a leg answers, then
        # ``on_auth_recovered()`` restarts SSE. The flag lives here, on the
        # poller, and is never latched.
        self._cycle = cycle
        self._on_auth_failed = on_auth_failed
        self._on_auth_recovered = on_auth_recovered
        self._polling_mode = polling_mode
        self._auth_failed = False
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
        """True while the poller is polling. False while auth-failed: the
        poller does not run then, only the slow probe does (R3, R6)."""
        with self._lock:
            if self._auth_failed:
                return False
            return self._thread is not None and self._thread.is_alive()

    def auth_failed(self) -> bool:
        """True while the client is in the auth-failed regime (R2-R4)."""
        with self._lock:
            return self._auth_failed

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
        # Fetch IMMEDIATELY on engage, then settle into the interval cadence
        # (qfg-41nh.10, matches sdk-go/java/net). The poller only engages when
        # SSE is already unavailable, so the held config is already suspect —
        # waiting a full interval before the first fetch put first-data-after-
        # SSE-loss at grace + interval (~180s) vs ~120s in sdk-go. The engage-
        # right-after-init case is harmless: the leg ETags turn it into a 304.
        while not stop_event.is_set() and not self._shutdown.is_set():
            outcome = self._poll_once()
            if outcome is not None and outcome.auth_status:
                if not self._run_auth_failed(stop_event, outcome):
                    return
                # Polling mode: resume at the interval, no immediate fetch.
            # Wait the interval AFTER the fetch; lets disengage() short-circuit
            # a sleeping cycle.
            if stop_event.wait(self._requested_wait("poll", self._interval)):
                return
            if self._shutdown.is_set():
                return

    def _run_auth_failed(self, stop_event: threading.Event, entering: CycleOutcome) -> bool:
        """Auth-failed regime (R3-R5), run on the poller thread. Returns True
        to keep polling on this thread (polling mode, after exit) and False
        when the thread must end (stopped, closed, or SSE mode after exit, where
        SSE is restarted and the poller goes back to its normal engage rule)."""
        with self._lock:
            self._auth_failed = True
        if self._on_auth_failed is not None:
            try:
                self._on_auth_failed(entering.auth_status, entering.auth_host)
            except Exception as e:  # noqa: BLE001 — must not kill the probe loop
                _LOG.debug("auth-failed entry hook raised: %s: %s", type(e).__name__, e)
        try:
            while True:
                period = random.uniform(AUTH_PROBE_MIN_SECONDS, AUTH_PROBE_MAX_SECONDS)
                if self._polling_mode:
                    period = max(period, self._interval)
                if stop_event.wait(self._requested_wait("probe", period)):
                    return False
                if self._shutdown.is_set():
                    return False
                outcome = self._poll_once()
                if outcome is not None and outcome.answered:
                    break
                _LOG.debug(
                    "[quonfig] auth-failed probe rejected (HTTP %s); next probe in %d-%ds",
                    outcome.auth_status if outcome is not None and outcome.auth_status else "error",
                    int(AUTH_PROBE_MIN_SECONDS),
                    int(AUTH_PROBE_MAX_SECONDS),
                )
        finally:
            with self._lock:
                self._auth_failed = False
                if not self._polling_mode and self._stop_event is stop_event:
                    # SSE mode: release the slot before SSE restarts, so the
                    # restarted stream's state edges can engage a new poller.
                    self._stop_event = None
                    self._thread = None
        if self._polling_mode:
            _LOG.info("[quonfig] config fetch accepted; leaving auth-failed and resuming polling")
            return True
        _LOG.info(
            "[quonfig] config fetch accepted; leaving auth-failed and resuming streaming "
            "and polling"
        )
        if self._on_auth_recovered is not None:
            try:
                self._on_auth_recovered()
            except Exception as e:  # noqa: BLE001
                _LOG.debug("auth-failed exit hook raised: %s: %s", type(e).__name__, e)
        return False

    def _requested_wait(self, kind: str, seconds: float) -> float:
        if self._wait_hook is not None:
            return self._wait_hook(kind, seconds)
        return seconds

    def _poll_once(self) -> Optional[CycleOutcome]:
        try:
            if self._cycle is not None:
                return self._cycle()
            if self._refresh is not None:
                # Hedged path (qfg-7h5d.1.14): the refresh callable installs
                # through the reject-older guard and fires on_config_update
                # itself, so there is nothing more to do here.
                self._refresh()
                return None
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
        return None
