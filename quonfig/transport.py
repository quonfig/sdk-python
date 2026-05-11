from __future__ import annotations

import base64
import logging
import threading
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Callable, List, Optional
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


class Transport:
    def __init__(
        self,
        api_urls: List[str],
        sdk_key: str,
        timeout: float = 10.0,
    ) -> None:
        self.api_urls = api_urls
        self.sdk_key = sdk_key
        self.timeout = timeout
        self._current_url_idx = 0
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
        return derive_stream_url(self._current_url())

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

        for _ in range(len(self.api_urls)):
            try:
                url = f"{self._current_url()}/api/v2/configs"
                response = self._session.get(url, headers=headers, timeout=self.timeout)
                if response.status_code == 304:
                    return None
                response.raise_for_status()
                envelope = ConfigEnvelope.from_dict(response.json())
                return envelope
            except (requests.ConnectionError, requests.Timeout):
                self._failover()
            except requests.HTTPError:
                self._failover()
        raise RuntimeError("All API URLs failed")


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
    ) -> None:
        self._transport = transport
        self._store = store
        self._interval = interval_seconds
        self._shutdown = shutdown_event
        self._on_config_update = on_config_update
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
                etag = self._store.get_etag()
                envelope = self._transport.fetch(etag=etag)
                if envelope is not None:
                    self._store.update(envelope)
                    if self._on_config_update is not None:
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
