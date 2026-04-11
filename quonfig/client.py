from __future__ import annotations

import contextlib
import logging
import os
import threading
from typing import Any, List, Optional

from .context import (
    clear_thread_context,
    get_thread_context,
    merge_contexts,
    set_thread_context,
)
from .evaluator import Evaluator
from .exceptions import (
    QuonfigInitTimeoutError,
    QuonfigKeyNotFoundError,
    QuonfigNotInitializedError,
)
from .resolver import LOG_LEVEL_ORDER, Resolver
from .store import ConfigStore
from .transport import Transport
from .types import Contexts

logger = logging.getLogger(__name__)

_NO_DEFAULT = object()

# Default API URL
_DEFAULT_API_URL = "https://api.quonfig.com"


class Quonfig:
    """
    Main Quonfig SDK client.

    Usage:
        client = Quonfig(sdk_key="sdk-...")
        client.init()
        value = client.get_string("my.key", default="fallback")
        enabled = client.is_feature_enabled("my.flag")
    """

    def __init__(
        self,
        sdk_key: Optional[str] = None,
        *,
        api_urls: Optional[List[str]] = None,
        init_timeout: float = 10.0,
        on_init_failure: str = "raise",  # "raise" | "return_zero_value"
        global_context: Optional[Contexts] = None,
        environment: Optional[str] = None,
        telemetry_url: Optional[str] = None,
        collect_evaluation_summaries: bool = True,
        context_upload_mode: str = "shapes_only",  # "none" | "shapes_only" | "periodic_example"
        on_no_default: str = "error",  # "error" | "warn" | "ignore"
        datadir: Optional[str] = None,
    ) -> None:
        # Resolve configuration from params or env vars
        self._sdk_key = sdk_key or os.environ.get("QUONFIG_SDK_KEY", "")
        self._environment = environment or os.environ.get("QUONFIG_ENVIRONMENT", "")
        self._datadir = datadir or os.environ.get("QUONFIG_DIR")

        if api_urls:
            self._api_urls = api_urls
        else:
            env_url = os.environ.get("QUONFIG_API_URL", "")
            if env_url:
                self._api_urls = [u.strip() for u in env_url.split(",") if u.strip()]
            else:
                self._api_urls = [_DEFAULT_API_URL]

        self._telemetry_url = telemetry_url or os.environ.get(
            "QUONFIG_TELEMETRY_URL", _DEFAULT_API_URL
        )
        self._init_timeout = init_timeout
        self._on_init_failure = on_init_failure
        self._on_no_default = on_no_default
        self._global_context: Contexts = global_context or {}

        self._store = ConfigStore()
        self._shutdown = threading.Event()
        self._initialized = threading.Event()
        self._init_error: Optional[Exception] = None

        # Will be set after init
        self._evaluator: Optional[Evaluator] = None
        self._resolver = Resolver(self._store)

        # Telemetry (optional)
        self._telemetry = None
        if collect_evaluation_summaries or context_upload_mode != "none":
            try:
                from .telemetry import TelemetryReporter

                self._telemetry = TelemetryReporter(
                    telemetry_url=self._telemetry_url,
                    sdk_key=self._sdk_key,
                    collect_evaluation_summaries=collect_evaluation_summaries,
                    collect_context_shapes=(context_upload_mode != "none"),
                )
            except Exception:
                pass  # Telemetry is optional

        # Transport (only if not datadir mode)
        self._transport: Optional[Transport] = None
        if not self._datadir and self._sdk_key:
            self._transport = Transport(
                api_urls=self._api_urls,
                sdk_key=self._sdk_key,
            )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def init(self) -> "Quonfig":
        """
        Block until first config load completes (or timeout).

        Raises QuonfigInitTimeoutError if init_timeout exceeded and
        on_init_failure="raise".
        """
        if self._datadir:
            self._load_from_datadir()
        elif self._transport:
            self._load_from_api()
        else:
            # No data source configured — mark initialized with empty store
            self._finish_init()

        return self

    def _load_from_datadir(self) -> None:
        from .datadir import load_datadir

        try:
            envelope = load_datadir(self._datadir, self._environment)
            self._store.update(envelope)
        except Exception as e:
            self._init_error = e
            logger.error("Failed to load datadir: %s", e)
        finally:
            self._finish_init()

    def _load_from_api(self) -> None:
        """Start background SSE thread; initial load done via polling thread."""
        assert self._transport is not None

        # Do an initial blocking fetch to populate the store
        try:
            envelope = self._transport.fetch()
            if envelope is not None:
                self._store.update(envelope)
            self._finish_init()
        except Exception as e:
            logger.warning("Initial fetch failed: %s — starting SSE anyway", e)
            self._finish_init()

        # Start SSE for live updates
        from .sse import SSEClient

        sse = SSEClient(self._transport, self._store, self._shutdown)
        sse.start()

        # Start polling as fallback
        self._transport.start_polling(self._store, self._shutdown)

        # Start telemetry
        if self._telemetry is not None:
            self._telemetry.start()

    def _finish_init(self) -> None:
        self._evaluator = Evaluator(self._store, self._environment)
        self._initialized.set()

    def _wait_initialized(self) -> None:
        if not self._initialized.is_set():
            ok = self._initialized.wait(timeout=self._init_timeout)
            if not ok:
                if self._on_init_failure == "raise":
                    raise QuonfigInitTimeoutError(
                        f"Quonfig did not initialize within {self._init_timeout}s"
                    )
                # return_zero_value: best effort with partial data
                self._finish_init()

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _effective_contexts(self, contexts: Optional[Contexts]) -> Contexts:
        """Merge global, thread-local, and per-call contexts."""
        parts = [self._global_context]
        thread_ctx = get_thread_context()
        if thread_ctx:
            parts.append(thread_ctx)
        if contexts:
            parts.append(contexts)
        return merge_contexts(*[p for p in parts if p])

    # ------------------------------------------------------------------
    # Core evaluate + resolve
    # ------------------------------------------------------------------

    def _get(self, key: str, contexts: Optional[Contexts] = None) -> Any:
        self._wait_initialized()
        assert self._evaluator is not None
        merged = self._effective_contexts(contexts)
        result = self._evaluator.evaluate(key, merged)

        # Record telemetry
        if self._telemetry is not None:
            self._telemetry.record_evaluation(result)
            if merged:
                self._telemetry.record_context(merged)

        if result.reason == "MISSING" or result.value is None:
            return _NO_DEFAULT

        try:
            return self._resolver.resolve(result.value, merged)
        except Exception as e:
            logger.warning("Error resolving value for key '%s': %s", key, e)
            return _NO_DEFAULT

    def _handle_missing(self, key: str, default: Any) -> Any:
        if default is not _NO_DEFAULT:
            return default
        if self._on_no_default == "error":
            raise QuonfigKeyNotFoundError(
                f"No value found for key '{key}' and no default was provided"
            )
        elif self._on_no_default == "warn":
            logger.warning("No value found for key '%s'", key)
        return None

    # ------------------------------------------------------------------
    # Typed getters
    # ------------------------------------------------------------------

    def get(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Any:
        """Get any config value by key, returning raw Python type."""
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            return self._handle_missing(key, default)
        return result

    def get_string(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Optional[str]:
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            val = self._handle_missing(key, default)
            return str(val) if val is not None else None
        return str(result) if result is not None else None

    def get_int(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Optional[int]:
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            val = self._handle_missing(key, default)
            return int(val) if val is not None else None
        try:
            return int(result)
        except (TypeError, ValueError):
            return None

    def get_float(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Optional[float]:
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            val = self._handle_missing(key, default)
            return float(val) if val is not None else None
        try:
            return float(result)
        except (TypeError, ValueError):
            return None

    def get_bool(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Optional[bool]:
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            val = self._handle_missing(key, default)
            return bool(val) if val is not None else None
        return bool(result)

    def get_string_list(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Optional[List[str]]:
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            val = self._handle_missing(key, default)
            if val is None:
                return None
            if isinstance(val, list):
                return [str(x) for x in val]
            return [str(val)]
        if isinstance(result, list):
            return [str(x) for x in result]
        return [str(result)] if result is not None else None

    def get_json(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Any:
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            return self._handle_missing(key, default)
        return result

    def get_duration(
        self,
        key: str,
        default: Any = _NO_DEFAULT,
        contexts: Optional[Contexts] = None,
    ) -> Optional[float]:
        """Get a duration value in seconds."""
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            val = self._handle_missing(key, default)
            return float(val) if val is not None else None
        try:
            return float(result)
        except (TypeError, ValueError):
            return None

    def is_feature_enabled(
        self,
        key: str,
        default: bool = False,
        contexts: Optional[Contexts] = None,
    ) -> bool:
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            return default
        if isinstance(result, bool):
            return result
        if isinstance(result, str):
            return result.lower() in ("true", "1", "yes", "on")
        return bool(result)

    def should_log(
        self,
        logger_name: str,
        desired_level: str,
        contexts: Optional[Contexts] = None,
    ) -> bool:
        """
        Return True if the given logger_name should log at desired_level.

        Walks the hierarchy from specific to general:
            log-levels.app.auth -> log-levels.app -> log-levels
        """
        desired_order = LOG_LEVEL_ORDER.get(desired_level.upper())
        if desired_order is None:
            return True  # Unknown level — log it

        # Build hierarchy of keys to check
        parts = logger_name.split(".")
        keys_to_check = []
        for i in range(len(parts), 0, -1):
            keys_to_check.append("log-levels." + ".".join(parts[:i]))
        keys_to_check.append("log-levels")

        for key in keys_to_check:
            result = self._get(key, contexts)
            if result is not _NO_DEFAULT and result is not None:
                configured_order = LOG_LEVEL_ORDER.get(str(result).upper())
                if configured_order is not None:
                    return desired_order >= configured_order
        # No config found — default to logging everything
        return True

    # ------------------------------------------------------------------
    # Context scoping
    # ------------------------------------------------------------------

    def with_context(self, contexts: Contexts) -> "BoundQuonfig":
        from .bound_client import BoundQuonfig

        return BoundQuonfig(self, contexts)

    @contextlib.contextmanager
    def scoped_context(self, contexts: Contexts):
        """Context manager that sets thread-local context for the duration."""
        old = get_thread_context()
        try:
            set_thread_context(contexts)
            yield self
        finally:
            if old is None:
                clear_thread_context()
            else:
                set_thread_context(old)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def keys(self) -> List[str]:
        return self._store.keys()

    def close(self) -> None:
        self._shutdown.set()
        if self._telemetry is not None:
            try:
                self._telemetry.stop()
            except Exception:
                pass

    def __enter__(self) -> "Quonfig":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
