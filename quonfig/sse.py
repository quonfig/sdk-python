from __future__ import annotations

import json
import logging
import random
import threading
from typing import Callable, Iterable, Iterator, List, Optional

import requests  # type: ignore[import-untyped]

from .store import ConfigStore
from .transport import Transport
from .types import ConfigEnvelope

_LOG = logging.getLogger(__name__)

# Public state vocabulary mirrors sdk-node so the cross-SDK chaos harness's
# expression vocabulary (`client.connectionState() == 'connected'`) maps the
# same way per-SDK.
SSEState = str  # one of: "connecting", "connected", "error", "disconnected"

# Reconnect floor after a stream that ended cleanly (server FIN / LB recycle).
# Mirrors sdk-go's runLoop: sleep a jittered ``delay/2 .. delay`` before every
# reconnect, with ``delay`` = sdk-go's 500 ms initial delay, and no growth on
# a clean EOF. Without it a 200-then-close server is hammered in a tight loop
# (qfg-goi1.2.12).
_CLEAN_EOF_RECONNECT_DELAY_S = 0.5

# Cap on the error-path reconnect backoff. Matches sdk-go (sse_client.go
# MaxDelay) and sdk-node (SSE_RECONNECT_MAX_DELAY_MS): 30 s, so the first
# attempt after a long outage clears lands within 30 s (qfg-q8lb).
_SSE_RECONNECT_MAX_DELAY_S = 30.0


# Upper bound for one read of the stream body. ``read1`` returns whatever has
# arrived (up to this), so a 13-byte keepalive is seen the moment it lands.
_READ_CHUNK_BYTES = 65536


def _iter_body(response: requests.Response) -> Iterator[bytes]:
    """Yield the stream body as it arrives.

    ``iter_content(n)`` on a close-delimited (non-chunked) response blocks
    until ``n`` bytes or EOF, so a lone keepalive could sit unread for many
    keepalive intervals. urllib3's ``read1`` (2.2+; the declared floor is
    2.3) returns as soon as any bytes are available, chunked or not. The
    ``iter_content`` branch is only for response objects without a urllib3
    ``raw`` (test doubles); it is not an old-urllib3 path."""
    raw = getattr(response, "raw", None)
    read1 = getattr(raw, "read1", None)
    if not callable(read1):
        yield from response.iter_content(chunk_size=128)
        return
    while True:
        chunk = read1(_READ_CHUNK_BYTES, decode_content=True)
        if not chunk:
            return
        yield chunk


class _FramelessStreamError(Exception):
    """A 200 stream that ended before its first valid frame: a failed attempt
    (qfg-goi1.10.44), handled exactly like a connect error."""


def _clean_eof_reconnect_sleep() -> float:
    delay = _CLEAN_EOF_RECONNECT_DELAY_S
    return delay / 2 + random.random() * delay / 2


def _close_response(response: requests.Response) -> None:
    """Close a streaming response, first unblocking any thread parked in its
    read (``HTTPResponse.shutdown``, urllib3 2.3+, the declared floor; a
    plain close does not reliably wake a blocked ``recv`` on Linux). The
    ``getattr`` guard only tolerates test doubles without a ``raw``."""
    try:
        shutdown = getattr(getattr(response, "raw", None), "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception as e:  # noqa: BLE001 — best effort; close() below still runs
        _LOG.debug("SSE response shutdown raised: %s: %s", type(e).__name__, e)
    try:
        response.close()
    except Exception as e:  # noqa: BLE001
        _LOG.debug("SSE response close raised: %s: %s", type(e).__name__, e)


class SSEClient:
    def __init__(
        self,
        transport: Transport,
        store: ConfigStore,
        shutdown_event: threading.Event,
        state_listener: Optional[Callable[[SSEState], None]] = None,
        on_config_update: Optional[Callable[[], None]] = None,
        install: Optional[Callable[[ConfigEnvelope], bool]] = None,
        record_refresh: Optional[Callable[[], None]] = None,
        wait_hook: Optional[Callable[[str, float], float]] = None,
        on_diagnostic: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.transport = transport
        # Test-only diagnostics hook (qfg-6vno.5), not public API: called with
        # ``"sse_connect_attempt"`` before every stream request and
        # ``"worker_restart"`` each time the loop restarts the worker after an
        # abnormal connection end. Feeds ``Quonfig._chaos_diagnostics()`` so
        # the chaos probe reads these counters from the SDK.
        self._on_diagnostic = on_diagnostic
        # Test seam (supervisor contract Test 8): maps each requested
        # reconnect sleep to the duration actually slept, so a test can record
        # the requested value and compress it. ``None`` in production.
        self._wait_hook = wait_hook
        self.store = store
        self.shutdown_event = shutdown_event
        self._state_listener = state_listener
        self._on_config_update = on_config_update
        # Liveness stamp for a received-and-processed SSE message that was a
        # guard no-op (equal-or-older): the stream proved live and the held
        # config current even though nothing installed, so it counts as a
        # successful refresh. On an accepted install, ``on_config_update``
        # stamps instead, so an install never double-stamps (qfg-41nh.11).
        self._record_refresh = record_refresh
        # Guarded install hook (qfg-7h5d.1.8). When provided, an SSE snapshot /
        # update is installed through the client's reject-older guard, which
        # returns whether the install was accepted; ``on_config_update`` fires
        # only on an accepted install so a stale (same-or-older) snapshot can't
        # flap an established client. Falls back to an unconditional store
        # update when no hook is wired (datadir / legacy callers).
        self._install = install
        self._thread: Optional[threading.Thread] = None
        self._stream_url_override: Optional[str] = None
        # The live stream response, kept so ``stop()`` can close it and unblock
        # the read at once instead of waiting for the next event / heartbeat or
        # the 60 s read timeout (qfg-goi1.2.13).
        self._response: Optional[requests.Response] = None
        self._response_lock = threading.Lock()
        # Whether the current connection has delivered a valid frame. Set as a
        # side effect by ``_parse_stream`` (not only via its return value), so
        # a stream that delivered frames and then died with an IO error (a
        # reset, a truncated chunk, a read timeout) still counts as a
        # successful run and resets the backoff (supervisor contract
        # Test 2 Reset; sdk-go sse_client.go connectOnce).
        self._frame_seen = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="quonfig-sse")
        self._thread.start()

    def stop(self) -> None:
        """Stop the stream: set the shutdown flag and close the live response,
        so the SSE thread's blocked read returns and the loop exits. The thread
        is a daemon, so this does not join it."""
        self.shutdown_event.set()
        with self._response_lock:
            response = self._response
            self._response = None
        if response is not None:
            _close_response(response)

    def join(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for the SSE thread to finish (after
        ``stop()``)."""
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _sleep(self, seconds: float) -> None:
        if self._wait_hook is not None:
            seconds = self._wait_hook("sse", seconds)
        self.shutdown_event.wait(seconds)

    def _emit(self, state: SSEState) -> None:
        if self._state_listener is None:
            return
        try:
            self._state_listener(state)
        except Exception as e:  # noqa: BLE001 — listener errors must not kill SSE loop
            _LOG.debug("SSE state listener raised: %s: %s", type(e).__name__, e)

    def _diag(self, kind: str) -> None:
        if self._on_diagnostic is None:
            return
        try:
            self._on_diagnostic(kind)
        except Exception as e:  # noqa: BLE001 — a diagnostics hook must not kill SSE
            _LOG.debug("SSE diagnostics hook raised: %s: %s", type(e).__name__, e)

    def is_alive(self) -> bool:
        """Whether the SSE worker thread is running (test-only diagnostics)."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _stream_url(self) -> str:
        # Test seam mirroring sdk-node's `__testStreamUrlOverride` so chaos
        # harness can route SSE through toxiproxy without DNS games.
        if self._stream_url_override:
            return self._stream_url_override
        transport_override = getattr(self.transport, "_Transport__test_stream_url_override", None)
        if transport_override:
            return transport_override
        return f"{self.transport._current_stream_url()}/api/v2/sse/config"

    def _parse_stream(self, chunks: Iterable[bytes], announce: bool) -> bool:
        """Parse one connection's ``text/event-stream`` body and return whether
        it delivered a valid frame (qfg-goi1.10.44).

        A frame is a complete block (ended by a blank line) that is either
        comment-only (the server's ``: keepalive``) or a ``data:`` block that
        decodes and validates as a config envelope. 200 headers alone, a block
        cut off by EOF, malformed data, a non-envelope payload and a block of
        other fields only are not frames. sseclient-py could not be used here:
        its ``events()`` never dispatches a block with no data, so a keepalive
        was invisible.

        At the first frame the connection counts as connected: it sets
        ``self._frame_seen`` (so the caller's backoff resets even if the stream
        later dies with an IO error, which never returns here) and, if
        ``announce``, the ``connected`` edge is emitted, before that frame's
        envelope installs.
        """
        frame_seen = False
        buf = b""
        data_lines: List[str] = []
        saw_comment = False
        saw_field = False

        def on_frame() -> None:
            nonlocal frame_seen
            if frame_seen:
                return
            frame_seen = True
            self._frame_seen = True
            if announce:
                self._emit("connected")

        def end_block() -> None:
            nonlocal saw_comment, saw_field
            data = "\n".join(data_lines) if data_lines else None
            comment_only = saw_comment and not saw_field
            data_lines.clear()
            saw_comment = saw_field = False
            if data is not None:
                self._handle_data(data, on_frame)
            elif comment_only:
                on_frame()

        for chunk in chunks:
            if self.shutdown_event.is_set():
                break
            if not chunk:
                continue
            buf += chunk
            lines = buf.splitlines(keepends=True)
            buf = b""
            # Carry a tail with no LF to the next chunk: it is unterminated, or
            # it ends in a CR whose LF may still be coming. (A CR-only stream
            # therefore lags one line; api-delivery sends LF.)
            if lines and not lines[-1].endswith(b"\n"):
                buf = lines.pop()
            for raw in lines:
                line = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
                if line == "":
                    end_block()
                elif line.startswith(":"):
                    saw_comment = True
                else:
                    saw_field = True
                    name, _, value = line.partition(":")
                    if value.startswith(" "):
                        value = value[1:]
                    if name == "data":
                        data_lines.append(value)
        # Anything still buffered was cut off by EOF: not a frame.
        return frame_seen

    def _handle_data(self, data: str, on_frame: Callable[[], None]) -> None:
        try:
            # from_wire rejects non-envelopes ({} / {"error": ..}), dropped
            # below like malformed JSON (qfg-9dxb.3).
            envelope = ConfigEnvelope.from_wire(json.loads(data))
        except Exception as e:
            _LOG.warning(
                "Quonfig SSE: dropping malformed event: %s: %s",
                type(e).__name__,
                e,
            )
            return
        # A valid envelope is a frame: connected before it installs.
        on_frame()
        try:
            if self._install is not None:
                installed = self._install(envelope)
            else:
                installed = self.store.update(envelope)
        except Exception as e:
            _LOG.warning(
                "Quonfig SSE: dropping event that failed to install: %s: %s",
                type(e).__name__,
                e,
            )
            return
        # Reject-older guard dropped a same-or-older snapshot: the held config
        # is unchanged, so do not fire the update callback (no flap). But the
        # message WAS received and processed, so the stream proved live and the
        # held config current: advance the liveness stamp (qfg-41nh.11).
        if not installed:
            if self._record_refresh is not None:
                self._record_refresh()
            return
        if self._on_config_update is not None:
            try:
                self._on_config_update()
            except Exception as e:  # noqa: BLE001 — supervisor MUST catch
                _LOG.error(
                    "Quonfig SSE: onConfigUpdate callback threw: "
                    "%s: %s — supervisor caught, continuing",
                    type(e).__name__,
                    e,
                )

    def _loop(self) -> None:
        backoff = 1.0
        # Bounded transparent reconnect (qfg-47c2.31, bounded by qfg-goi1.10.44
        # per the supervisor contract Rule). After a connection that delivered
        # a frame ends with a clean EOF (server FIN, LB recycle), the next
        # attempt runs without a public state edge: the cached config is
        # unchanged, so a connected->connecting edge would be a lie that costs
        # chaos probes a spurious Layer 1 restart count. The hold lasts one
        # connection: it must deliver a frame (still connected, no edge) or it
        # is a failed attempt and reports ``error`` at once. ``connected`` is
        # never held across two frameless connections.
        transparent_reconnect = False
        while not self.shutdown_event.is_set():
            # On a transparent reconnect the public state is still
            # "connected", so neither "connecting" nor a repeated
            # "connected" is emitted for it (qfg-goi1.2.12).
            silent = transparent_reconnect
            if not silent:
                self._emit("connecting")
            transparent_reconnect = False
            response: Optional[requests.Response] = None
            try:
                url = self._stream_url()
                headers = self.transport._headers({"Accept": "text/event-stream"})
                self._diag("sse_connect_attempt")
                response = requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=(5, 60),
                )
                with self._response_lock:
                    self._response = response
                if self.shutdown_event.is_set():
                    # stop() ran while we were connecting: it could not see
                    # this response, so close it here.
                    break
                response.raise_for_status()
                # 200 headers alone are not "connected": that waits for the
                # first valid frame (qfg-goi1.10.44). Between the headers and
                # the frame the public state stays what it was.
                self._frame_seen = False
                try:
                    self._parse_stream(_iter_body(response), announce=not silent)
                finally:
                    # A frame made this a successful run, however the stream
                    # then ended (clean EOF or IO error).
                    if self._frame_seen:
                        backoff = 1.0
                if self.shutdown_event.is_set():
                    break
                if not self._frame_seen:
                    raise _FramelessStreamError("stream ended before its first frame")
                # Clean EOF after a frame: reconnect transparently (no edge).
                _LOG.debug(
                    "Quonfig SSE: stream ended cleanly mid-session, reconnecting transparently"
                )
                transparent_reconnect = True
                self._sleep(_clean_eof_reconnect_sleep())
            except Exception as e:
                if self.shutdown_event.is_set():
                    break
                _LOG.debug(
                    "Quonfig SSE connection error, reconnecting: %s: %s",
                    type(e).__name__,
                    e,
                )
                self._emit("error")
                self._diag("worker_restart")
                # Exponential backoff with jitter
                self._sleep(backoff)
                backoff = min(
                    backoff * 2 * (0.8 + 0.4 * random.random()), _SSE_RECONNECT_MAX_DELAY_S
                )
            finally:
                if response is not None:
                    with self._response_lock:
                        if self._response is response:
                            self._response = None
                    _close_response(response)
        self._emit("disconnected")
