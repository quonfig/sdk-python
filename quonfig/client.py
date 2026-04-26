from __future__ import annotations

import contextlib
import logging
import os
import threading
import warnings
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from .bound_client import BoundQuonfig

from .context import (
    clear_thread_context,
    get_thread_context,
    merge_contexts,
    set_thread_context,
)
from .evaluator import Evaluator
from .exceptions import (
    QuonfigDecryptionError,
    QuonfigEnvVarNotSetError,
    QuonfigInitTimeoutError,
    QuonfigKeyNotFoundError,
)
from .resolver import LOG_LEVEL_ORDER, Resolver
from .store import ConfigStore
from .transport import Transport
from .types import (
    QUONFIG_SDK_LOGGING_CONTEXT_KEY_PROP,
    QUONFIG_SDK_LOGGING_CONTEXT_NAME,
    Contexts,
)

logger = logging.getLogger(__name__)

_NO_DEFAULT = object()

# Default API URL
_DEFAULT_API_URL = "https://api.quonfig.com"
_DEFAULT_TELEMETRY_URL = "https://telemetry.quonfig.com"


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
        init_timeout: Optional[float] = None,
        # Cross-SDK alias for `init_timeout` — mirrors the YAML
        # `client_overrides.initialization_timeout_sec` knob the shared
        # integration suite uses. Wins over `init_timeout` if both are set.
        initialization_timeout_sec: Optional[float] = None,
        on_init_failure: str = "raise",  # "raise" | "return" | "return_zero_value"
        global_context: Optional[Contexts] = None,
        environment: Optional[str] = None,
        telemetry_url: Optional[str] = None,
        collect_evaluation_summaries: bool = True,
        # "none" | "shapes_only" | "periodic_example"
        context_upload_mode: str = "periodic_example",
        on_no_default: str = "error",  # "error" | "warn" | "ignore"
        datadir: Optional[str] = None,
        # Cross-SDK alias: when set, behaves as a single-element `api_urls`.
        # When passed alongside `datadir` the HTTP source wins, matching the
        # YAML suite's expectation that `prefab_api_url` + a tiny
        # `initialization_timeout_sec` actually exercises an init timeout.
        prefab_api_url: Optional[str] = None,
        logger_key: Optional[str] = None,
    ) -> None:
        # Resolve configuration from params or env vars
        self._sdk_key = sdk_key or os.environ.get("QUONFIG_SDK_KEY", "")
        self._environment = environment or os.environ.get("QUONFIG_ENVIRONMENT", "")
        # `prefab_api_url` (cross-SDK) overrides `datadir` so the test suite's
        # init-timeout cases can exercise real HTTP behavior even when the
        # datadir is also passed through.
        self._datadir = (
            None if prefab_api_url else (datadir or os.environ.get("QUONFIG_DIR"))
        )

        if api_urls:
            self._api_urls = api_urls
        elif prefab_api_url:
            self._api_urls = [prefab_api_url]
        else:
            env_urls = os.environ.get("QUONFIG_API_URLS", "")
            if not env_urls:
                legacy = os.environ.get("QUONFIG_API_URL", "")
                if legacy:
                    warnings.warn(
                        "QUONFIG_API_URL is deprecated; use QUONFIG_API_URLS "
                        "(comma-separated) to match the other Quonfig SDKs. "
                        "Support for QUONFIG_API_URL will be removed in a "
                        "future release.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    env_urls = legacy
            if env_urls:
                self._api_urls = [u.strip() for u in env_urls.split(",") if u.strip()]
            else:
                self._api_urls = [_DEFAULT_API_URL]

        self._telemetry_url = telemetry_url or os.environ.get(
            "QUONFIG_TELEMETRY_URL", _DEFAULT_TELEMETRY_URL
        )
        # `initialization_timeout_sec` is the cross-SDK alias and wins; fall
        # back to `init_timeout` and finally the historical 10s default.
        if initialization_timeout_sec is not None:
            self._init_timeout = float(initialization_timeout_sec)
        elif init_timeout is not None:
            self._init_timeout = float(init_timeout)
        else:
            self._init_timeout = 10.0

        # Accept the YAML keyword form (`:return`) and the cross-SDK
        # short alias (`return`) on top of the historical
        # `return_zero_value`.
        normalized_on_init = (on_init_failure or "raise").lstrip(":").lower()
        if normalized_on_init == "return":
            normalized_on_init = "return_zero_value"
        self._on_init_failure = normalized_on_init
        self._on_no_default = on_no_default
        self._global_context: Contexts = global_context or {}
        self._logger_key = logger_key

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
                    context_upload_mode=context_upload_mode,
                )
            except Exception:
                pass  # Telemetry is optional

        # Transport: stand it up whenever the caller wired an HTTP source,
        # even in the explicit-`prefab_api_url` case where there's no
        # sdk_key (the integration test points at staging-prefab.cloud
        # specifically to exercise an init-timeout, not to fetch real
        # configs). When only datadir is configured, no transport is needed.
        # The request timeout is intentionally independent of
        # initialization_timeout_sec: capping it at a sub-second
        # init-timeout would surface a tiny init as a generic
        # `requests.Timeout` from the background fetch instead of letting
        # `_wait_initialized` raise `QuonfigInitTimeoutError`.
        self._transport: Optional[Transport] = None
        if prefab_api_url or (not self._datadir and self._sdk_key):
            self._transport = Transport(
                api_urls=self._api_urls,
                sdk_key=self._sdk_key,
            )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def init(self) -> "Quonfig":
        """
        Kick off the first config load.

        For datadir mode the load is synchronous and ``init()`` returns
        once the store is populated. For HTTP mode the fetch runs on a
        background thread so a tiny ``init_timeout`` can be enforced
        lazily by ``_wait_initialized`` on the first getter call —
        raising ``QuonfigInitTimeoutError`` when ``on_init_failure``
        is ``"raise"``.
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
            envelope = load_datadir(self._datadir or "", self._environment)
            self._store.update(envelope)
        except Exception as e:
            self._init_error = e
            logger.error("Failed to load datadir: %s", e)
            self._finish_init()
            raise
        else:
            self._finish_init()

        # Start telemetry (mirrors _load_from_api); guards for disabled telemetry
        # are handled in __init__ where self._telemetry is set to None.
        if self._telemetry is not None:
            self._telemetry.start()

    def _load_from_api(self) -> None:
        """Run the initial fetch on a background thread.

        Doing the fetch off the calling thread is what lets a small
        ``init_timeout`` actually surface as ``QuonfigInitTimeoutError``
        — otherwise ``init()`` would block on ``Transport.fetch`` and
        ``_wait_initialized`` would never see an unset event.
        """
        assert self._transport is not None
        # Bind to a local so mypy narrows inside the nested closure below;
        # `self._transport` is reread through `self` and would lose its
        # narrowing across the closure boundary.
        transport = self._transport

        def _initial_fetch() -> None:
            try:
                envelope = transport.fetch()
                if envelope is not None:
                    self._store.update(envelope)
            except Exception as e:
                logger.warning("Initial fetch failed: %s — starting SSE anyway", e)
            finally:
                self._finish_init()

        threading.Thread(
            target=_initial_fetch, daemon=True, name="quonfig-init"
        ).start()

        # Start SSE for live updates
        from .sse import SSEClient

        sse = SSEClient(transport, self._store, self._shutdown)
        sse.start()

        # Start polling as fallback
        transport.start_polling(self._store, self._shutdown)

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

        if result.reason == "MISSING" or result.value is None:
            return _NO_DEFAULT

        try:
            resolved = self._resolver.resolve(result.value, merged, config_key=key)
        except (QuonfigEnvVarNotSetError, QuonfigDecryptionError):
            raise
        except Exception as e:
            logger.warning("Error resolving value for key '%s': %s", key, e)
            return _NO_DEFAULT

        # Record telemetry after resolving so resolved_value is available
        if self._telemetry is not None:
            result.resolved_value = resolved
            self._telemetry.record_evaluation(result)
            if merged:
                self._telemetry.record_context(merged)

        return resolved

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
            # Coercion failed (e.g. env-var-provided value is not a valid int)
            return self._handle_missing(key, default)

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
        """Returns True only if the config is a boolean True value.
        Returns False for missing keys, non-boolean types, or boolean False."""
        result = self._get(key, contexts)
        if result is _NO_DEFAULT:
            return default
        if isinstance(result, bool):
            return result
        if isinstance(result, str):
            if result.lower() == "true":
                return True
            if result.lower() == "false":
                return False
        # Non-boolean types (int, float, list, dict, etc.) return False
        return False

    @property
    def logger_key(self) -> Optional[str]:
        """The config key used by ``should_log(logger_path=...)`` to look up
        per-logger levels. ``None`` unless set at construction time."""
        return self._logger_key

    def should_log(
        self,
        config_key: Optional[str] = None,
        desired_level: Optional[str] = None,
        contexts: Optional[Contexts] = None,
        *,
        logger_path: Optional[str] = None,
    ) -> bool:
        """Return True if a message at ``desired_level`` should be emitted.

        Two shapes are supported:

        1. ``should_log(config_key="log-level.my-app", desired_level="info")``
           — primitive. Evaluates the named config as a log level. The caller
           is responsible for any per-logger routing. The full stored key is
           required; the SDK does NOT auto-prefix "log-level.".

        2. ``should_log(logger_path="MyApp.Services.Auth", desired_level="info")``
           — convenience. Requires ``logger_key`` on the Quonfig constructor.
           The SDK evaluates ``logger_key`` with
           ``contexts["quonfig-sdk-logging"] = {"key": logger_path}`` merged
           in, so a single log-level config can drive per-logger overrides
           via the normal rule engine. ``logger_path`` is passed through
           verbatim — no normalization.

        Raises ``ValueError`` if neither or both of ``config_key`` /
        ``logger_path`` are provided, or if ``logger_path`` is provided
        without a configured ``logger_key``.
        """
        if desired_level is None:
            raise ValueError("should_log requires `desired_level`.")

        if config_key is not None and logger_path is not None:
            raise ValueError(
                "should_log: pass either `config_key` or `logger_path`, not both."
            )

        resolved_contexts = contexts

        if logger_path is not None:
            if not self._logger_key:
                raise ValueError(
                    "should_log(logger_path=...) requires the `logger_key` option on the "
                    "Quonfig constructor. Pass `logger_key=\"log-level.<your-app>\"` or "
                    "use the `config_key=...` form instead."
                )
            resolved_config_key: str = self._logger_key
            logger_ctx: Contexts = {
                QUONFIG_SDK_LOGGING_CONTEXT_NAME: {
                    QUONFIG_SDK_LOGGING_CONTEXT_KEY_PROP: logger_path
                }
            }
            from .context import merge_contexts as _merge

            resolved_contexts = _merge(contexts or {}, logger_ctx)
        elif config_key is not None:
            resolved_config_key = config_key
        else:
            raise ValueError(
                "should_log requires either `config_key` or `logger_path`."
            )

        desired_order = LOG_LEVEL_ORDER.get(desired_level.upper())
        if desired_order is None:
            # Unknown desired level — log it (match Go/Node/Ruby).
            return True

        # Evaluate the config; any error (missing, resolver failure) → log it.
        try:
            result = self._get(resolved_config_key, resolved_contexts)
        except Exception:
            return True

        if result is _NO_DEFAULT or result is None:
            return True

        configured_order = LOG_LEVEL_ORDER.get(str(result).upper())
        if configured_order is None:
            return True

        return desired_order >= configured_order

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
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass

    def __enter__(self) -> "Quonfig":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
