from __future__ import annotations

import json
import logging
import random
import threading
from typing import Callable, Optional

import requests  # type: ignore[import-untyped]
import sseclient  # type: ignore

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


def _clean_eof_reconnect_sleep() -> float:
    delay = _CLEAN_EOF_RECONNECT_DELAY_S
    return delay / 2 + random.random() * delay / 2


def _close_response(response: requests.Response) -> None:
    """Close a streaming response, first unblocking any thread parked in its
    read (urllib3 >= 2.3 ``HTTPResponse.shutdown``; a plain close does not
    reliably wake a blocked ``recv`` on Linux)."""
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
    ) -> None:
        self.transport = transport
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

    def _emit(self, state: SSEState) -> None:
        if self._state_listener is None:
            return
        try:
            self._state_listener(state)
        except Exception as e:  # noqa: BLE001 — listener errors must not kill SSE loop
            _LOG.debug("SSE state listener raised: %s: %s", type(e).__name__, e)

    def _stream_url(self) -> str:
        # Test seam mirroring sdk-node's `__testStreamUrlOverride` so chaos
        # harness can route SSE through toxiproxy without DNS games.
        if self._stream_url_override:
            return self._stream_url_override
        transport_override = getattr(self.transport, "_Transport__test_stream_url_override", None)
        if transport_override:
            return transport_override
        return f"{self.transport._current_stream_url()}/api/v2/sse/config"

    def _loop(self) -> None:
        backoff = 1.0
        # Transparent-reconnect flag: when sseclient.events() exits cleanly
        # mid-stream (e.g. server clean FIN, sseclient-py internal EOF after
        # an event batch), we re-attempt without flipping public state. The
        # connection drop is real but the cached config is unchanged, so a
        # connected->connecting edge would be a lie that costs chaos probes
        # a spurious Layer 1 restart count (qfg-47c2.31).
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
                client = sseclient.SSEClient(response)  # type: ignore[arg-type]
                backoff = 1.0  # Reset on successful connection
                if not silent:
                    self._emit("connected")
                for event in client.events():
                    if self.shutdown_event.is_set():
                        break
                    if event.data:
                        try:
                            # from_wire rejects non-envelopes ({} / {"error": ..}),
                            # dropped below like malformed JSON (qfg-9dxb.3).
                            envelope = ConfigEnvelope.from_wire(json.loads(event.data))
                            if self._install is not None:
                                installed = self._install(envelope)
                            else:
                                installed = self.store.update(envelope)
                        except Exception as e:
                            _LOG.warning(
                                "Quonfig SSE: dropping malformed event: %s: %s",
                                type(e).__name__,
                                e,
                            )
                            continue
                        # Reject-older guard dropped a same-or-older snapshot —
                        # the held config is unchanged, so do not fire the
                        # update callback (no flap). But the message WAS
                        # received and processed, so the stream proved live and
                        # the held config current: advance the liveness stamp
                        # (qfg-41nh.11).
                        if not installed:
                            if self._record_refresh is not None:
                                self._record_refresh()
                            continue
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
                # The events() generator returned without raising. That means
                # sseclient saw a clean EOF on the underlying response — the
                # server-side stream is closed but our cached config is
                # intact. Reconnect transparently (no "connecting" edge).
                if not self.shutdown_event.is_set():
                    _LOG.debug(
                        "Quonfig SSE: stream ended cleanly mid-session, reconnecting transparently"
                    )
                    transparent_reconnect = True
                    self.shutdown_event.wait(_clean_eof_reconnect_sleep())
            except Exception as e:
                if self.shutdown_event.is_set():
                    break
                _LOG.debug(
                    "Quonfig SSE connection error, reconnecting: %s: %s",
                    type(e).__name__,
                    e,
                )
                self._emit("error")
                # Exponential backoff with jitter
                self.shutdown_event.wait(backoff)
                backoff = min(backoff * 2 * (0.8 + 0.4 * random.random()), 60.0)
            finally:
                if response is not None:
                    with self._response_lock:
                        if self._response is response:
                            self._response = None
                    _close_response(response)
        self._emit("disconnected")
