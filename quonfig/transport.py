from __future__ import annotations

import base64
import threading
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests  # type: ignore[import-untyped]

from .types import ConfigEnvelope


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

    def _auth_header(self) -> str:
        credentials = base64.b64encode(f"1:{self.sdk_key}".encode()).decode()
        return f"Basic {credentials}"

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {
            "Authorization": self._auth_header(),
            "X-Quonfig-SDK-Version": f"python/{QUONFIG_VERSION}",
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

    def start_polling(
        self,
        store: "ConfigStore",
        shutdown_event: threading.Event,
        interval: float = 60.0,
    ) -> threading.Thread:
        """Start a daemon thread that polls for config updates every `interval` seconds."""

        def _poll_loop() -> None:
            while not shutdown_event.is_set():
                shutdown_event.wait(interval)
                if shutdown_event.is_set():
                    break
                try:
                    etag = store.get_etag()
                    envelope = self.fetch(etag=etag)
                    if envelope is not None:
                        store.update(envelope)
                except Exception:
                    pass  # Polling errors are non-fatal; SSE is primary path

        t = threading.Thread(target=_poll_loop, daemon=True, name="quonfig-poll")
        t.start()
        return t
