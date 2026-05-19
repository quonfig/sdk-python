from __future__ import annotations

import contextlib
import datetime
import logging
import os
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from collections.abc import Callable

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
from .resolver import LOG_LEVEL_ORDER, Resolver, compute_reportable_value
from .store import ConfigStore
from .transport import FallbackPoller, Transport
from .types import (
    QUONFIG_SDK_LOGGING_CONTEXT_KEY_PROP,
    QUONFIG_SDK_LOGGING_CONTEXT_NAME,
    Contexts,
    EvaluationDetails,
)

logger = logging.getLogger(__name__)

_NO_DEFAULT = object()


def _coerce_value(value: Any, expected_type: str) -> tuple[Any, bool]:
    """Coerce a resolved native value to the caller's expected type.

    Returns ``(coerced, True)`` on success, ``(None, False)`` on a hard
    type mismatch. ``expected_type`` mirrors the evaluator's value-type
    vocabulary plus an "any" passthrough for ``get_json``.

    Behavior matches the existing ``get_int`` / ``get_string`` permissive
    path: numeric strings coerce to ints/floats, non-bool values coerce
    to bool only via ``bool()`` semantics for the existing ``get_bool``,
    and lists go through ``get_string_list`` style ``str()`` coercion.
    """
    if expected_type == "any":
        return value, True
    if expected_type == "bool":
        if isinstance(value, bool):
            return value, True
        if isinstance(value, str):
            lowered = value.lower()
            if lowered == "true":
                return True, True
            if lowered == "false":
                return False, True
        return None, False
    if expected_type == "string":
        if isinstance(value, str):
            return value, True
        # Match get_string's permissive str() coercion.
        return str(value), True
    if expected_type == "int":
        if isinstance(value, bool):
            # bool is a subclass of int in Python — reject explicitly so
            # asking for an int doesn't silently accept True/False.
            return None, False
        try:
            return int(value), True
        except (TypeError, ValueError):
            return None, False
    if expected_type == "float":
        if isinstance(value, bool):
            return None, False
        try:
            return float(value), True
        except (TypeError, ValueError):
            return None, False
    if expected_type == "string_list":
        if isinstance(value, list):
            return [str(x) for x in value], True
        return None, False
    return value, True


# Default domain that governs the api/sse/telemetry URL defaults. A single
# `QUONFIG_DOMAIN` env var lets ops point staging-hosted services at the
# staging control plane without per-URL overrides — this mirrors the CLI
# (`cli/src/util/domain-urls.ts`) and the rest of the SDK fleet.
_DEFAULT_DOMAIN = "quonfig.com"


def _derive_defaults(domain: str) -> tuple[List[str], str]:
    """Derive (api_urls, telemetry_url) from a domain.

    Two api_urls (primary + secondary) so the transport's failover loop has
    something to fail over to. SSE URLs are derived from api_urls at use
    time by `Transport._current_stream_url` — no separate domain-derived
    SSE URL is stored.
    """
    api_urls = [f"https://primary.{domain}", f"https://secondary.{domain}"]
    telemetry_url = f"https://telemetry.{domain}"
    return api_urls, telemetry_url


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
        # Layer 2 HTTP fallback poller. Off-by-default-when-SSE-is-up: only
        # engages on initial-SSE-failure or after a sustained disconnect
        # (`fallback_poll_interval_ms` * 2 grace). Replaces the previous
        # always-on parallel poll (qfg-47c2.8).
        fallback_poll_enabled: bool = True,
        fallback_poll_interval_ms: int = 60000,
        # Cross-SDK observability hooks (mirror sdk-go's WithOnConfigUpdate /
        # WithSSEStateCallback and sdk-node's onConfigUpdate /
        # onSSEConnectionStateChange). Fire on each successful config install
        # and each SSE connection-state edge respectively. Exceptions thrown by
        # caller callbacks are caught by the SDK supervisor (chaos scenario 10).
        on_config_update: "Optional[Callable[[], None]]" = None,
        on_sse_connection_state_change: "Optional[Callable[[str], None]]" = None,
        # Opt-in datadir auto-reload (qfg-mol-3gy). When ``True`` in datadir
        # mode, the SDK watches the resolved datadir via ``watchfiles`` and
        # re-runs ``load_datadir`` on debounced bursts. Behavior:
        #
        #   * Default ``False`` — datadir mode stays silent until callers
        #     opt in. Mirrors sdk-node's ``dataDirAutoReload``.
        #   * Parse-then-swap: a mid-write / truncated envelope logs and is
        #     dropped; the previously-installed envelope keeps serving and
        #     ``on_config_update`` does NOT fire on parse failure.
        #   * Graceful degrade on read-only / immutable filesystems: if
        #     watcher registration fails (missing path, read-only fs), the
        #     SDK logs a warning and continues without auto-reload — it
        #     does NOT raise from ``init()``.
        #   * ``close()`` signals the watcher's stop event and joins the
        #     daemon thread (≤2s); no separate handle to manage.
        #   * Symlinked datadirs are resolved at start time — edits to the
        #     real target are detected; atomic retargets of the link itself
        #     are not.
        data_dir_auto_reload: bool = False,
        # Debounce window (ms) for ``data_dir_auto_reload``. Default ``200`` —
        # long enough to coalesce the 3–5 events editors emit on an atomic
        # save, short enough that interactive edits feel immediate. Has no
        # effect when ``data_dir_auto_reload`` is ``False``. See the README
        # "Datadir mode: auto-reload on file changes" section and
        # https://docs.quonfig.com/docs/how-tos/open-source-local for the
        # cross-SDK story.
        data_dir_auto_reload_debounce_ms: int = 200,
        # Dev-only: when true (or env var ``QUONFIG_DEV_CONTEXT=true``),
        # the SDK reads the per-domain tokens file written by ``qfg login``
        # (``~/.quonfig/tokens.json`` for production,
        # ``tokens-<domain-with-dashes>.json`` for staging) and merges
        # ``{"quonfig-user": {"email": ...}}`` into the global context.
        # Customer-supplied ``quonfig-user`` keys win on collision.
        # Mirrors sdk-node/sdk-go/sdk-ruby.
        enable_quonfig_user_context: bool = False,
    ) -> None:
        # Resolve configuration from params or env vars
        self._sdk_key = sdk_key or os.environ.get("QUONFIG_SDK_KEY", "")
        self._environment = environment or os.environ.get("QUONFIG_ENVIRONMENT", "")
        # `prefab_api_url` (cross-SDK) overrides `datadir` so the test suite's
        # init-timeout cases can exercise real HTTP behavior even when the
        # datadir is also passed through.
        self._datadir = None if prefab_api_url else (datadir or os.environ.get("QUONFIG_DIR"))

        # `QUONFIG_DOMAIN` governs both api_urls and telemetry_url defaults
        # so a single env var flips a service between prod and staging.
        # Explicit kwargs (`api_urls=`, `telemetry_url=`) remain the local-dev
        # escape hatch and supersede the env-derived defaults.
        domain = os.environ.get("QUONFIG_DOMAIN", "").strip() or _DEFAULT_DOMAIN
        default_api_urls, default_telemetry_url = _derive_defaults(domain)

        if api_urls:
            self._api_urls = api_urls
        elif prefab_api_url:
            self._api_urls = [prefab_api_url]
        else:
            self._api_urls = default_api_urls

        self._telemetry_url = telemetry_url or default_telemetry_url
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
        # Dev-context injection (qfg-jopa): mirror sdk-node/go/ruby behavior.
        # Customer-supplied `global_context` wins on collision because it
        # passes second to merge_contexts (later-wins).
        dev_context_enabled = (
            enable_quonfig_user_context or os.environ.get("QUONFIG_DEV_CONTEXT") == "true"
        )
        dev_context: Optional[Contexts] = None
        if dev_context_enabled:
            from .dev_context import load_quonfig_user_context

            dev_context = load_quonfig_user_context(self._api_urls)
        self._global_context = merge_contexts(dev_context or {}, global_context or {})
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

        # Layer 2 fallback poller state. The poller itself is only constructed
        # once a transport exists; the state vars below drive the engage/
        # disengage decision when SSE state changes arrive.
        self._fallback_poll_enabled = fallback_poll_enabled
        self._fallback_poll_interval_ms = fallback_poll_interval_ms
        self._fallback_poller: Optional[FallbackPoller] = None
        self._sse_ever_connected = False
        self._fallback_engage_timer: Optional[threading.Timer] = None
        self._fallback_lock = threading.Lock()
        self._on_config_update = on_config_update
        self._on_sse_connection_state_change = on_sse_connection_state_change
        self._data_dir_auto_reload = data_dir_auto_reload
        self._data_dir_auto_reload_debounce_ms = data_dir_auto_reload_debounce_ms
        self._datadir_watcher: Optional[Any] = None  # quonfig.datadir_watcher.DatadirWatcher

        # Customer-visible health primitives (qfg-47c2.15). Stamped on every
        # successful install (datadir load, initial fetch, SSE event, fallback
        # poll) via `_fire_on_config_update`. `_last_sse_state` records the
        # most recent SSE state edge so `connection_state()` can distinguish
        # `connected` from `disconnected` without re-deriving it from the
        # transport. Datadir-only mode has no SSE — we mark it `connected`
        # post-install since the data source is local.
        self._last_successful_refresh: Optional[datetime.datetime] = None
        self._last_sse_state: Optional[str] = None
        self._health_lock = threading.Lock()
        if self._transport is not None and self._fallback_poll_enabled:
            self._fallback_poller = FallbackPoller(
                transport=self._transport,
                store=self._store,
                interval_seconds=self._fallback_poll_interval_ms / 1000.0,
                shutdown_event=self._shutdown,
                on_config_update=self._fire_on_config_update,
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

    def _fire_on_config_update(self) -> None:
        """Invoke the user's on_config_update callback, swallowing any
        exceptions and logging them. Mirrors sdk-node's `invokeOnConfigUpdate`
        — chaos scenario 10 requires the SDK supervisor to catch user-callback
        throws so the rest of the SDK keeps running.

        Also stamps `last_successful_refresh()` — this is the single central
        post-install hook (datadir, initial fetch, SSE event, fallback poll
        all funnel through here), so it's the right place to record the
        wall-clock time of the most recent install.
        """
        with self._health_lock:
            self._last_successful_refresh = datetime.datetime.now(datetime.timezone.utc)
        if self._on_config_update is None:
            return
        try:
            self._on_config_update()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Quonfig: onConfigUpdate callback threw: %s: %s — supervisor caught, continuing",
                type(e).__name__,
                e,
            )

    def _load_from_datadir(self) -> None:
        from .datadir import load_datadir

        try:
            envelope = load_datadir(self._datadir or "", self._environment)
            self._store.update(envelope)
            self._fire_on_config_update()
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

        # Opt-in filesystem watcher (qfg-mol-3gy). Started after the first
        # install so the initial envelope is in place before any reload can
        # race the load. Registration failures (read-only fs, missing path)
        # log and downgrade — the SDK keeps serving the initial envelope.
        if self._data_dir_auto_reload and self._datadir:
            self._start_datadir_watcher()

    def _start_datadir_watcher(self) -> None:
        from .datadir_watcher import DatadirWatcher

        def _on_error(err: BaseException) -> None:
            logger.warning("Quonfig datadir watcher error: %s: %s", type(err).__name__, err)

        watcher = DatadirWatcher(
            datadir=self._datadir or "",
            debounce_ms=self._data_dir_auto_reload_debounce_ms,
            on_change=self._reload_datadir,
            on_error=_on_error,
        )
        if not watcher.start():
            logger.warning(
                "Quonfig data_dir_auto_reload requested but watcher registration failed; "
                "continuing without auto-reload"
            )
            return
        self._datadir_watcher = watcher

    def _reload_datadir(self) -> None:
        """Re-read the datadir into a fresh envelope and atomically install it.

        Parse-then-swap: build the new envelope first, then call
        `_store.update` (which already takes the store lock). On any failure
        (mid-write JSON garble, RuntimeError from the loader), keep the
        previous envelope and do NOT fire `on_config_update`.
        """
        if self._shutdown.is_set():
            return
        from .datadir import load_datadir

        try:
            envelope = load_datadir(self._datadir or "", self._environment)
        except Exception as e:  # noqa: BLE001 — parse-then-swap: never expose broken state
            logger.warning(
                "Quonfig datadir reload failed; keeping previous envelope: %s: %s",
                type(e).__name__,
                e,
            )
            return
        self._store.update(envelope)
        self._fire_on_config_update()

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
                    self._fire_on_config_update()
            except Exception as e:
                logger.warning("Initial fetch failed: %s — starting SSE anyway", e)
            finally:
                self._finish_init()

        threading.Thread(target=_initial_fetch, daemon=True, name="quonfig-init").start()

        # Start SSE for live updates. SSE state edges drive the Layer 2
        # fallback poller (engage on initial failure / sustained disconnect,
        # disengage on recovery) — see `_handle_sse_state_change`.
        from .sse import SSEClient

        self._sse = SSEClient(
            transport,
            self._store,
            self._shutdown,
            state_listener=self._handle_sse_state_change,
            on_config_update=self._fire_on_config_update,
        )
        self._sse.start()

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
            # Redact selectedValue for confidential / encrypted values before
            # the eval-summary aggregator sees it (matches Reforge SDK
            # reportable_wrapped_value pattern).
            result.reportable_value = compute_reportable_value(result.value)
            self._telemetry.record_evaluation(result)
            if merged:
                self._telemetry.record_context(merged)

        return resolved

    def _telemetry_reason_to_string(self, telemetry_reason: int, eval_reason: str) -> str:
        """Translate the internal telemetry-reason code to the OF-aligned
        EvaluationDetails ``reason`` string. Falls back to ``EvalResult.reason``
        when the telemetry code is unset (0)."""
        if telemetry_reason == 1:
            return "STATIC"
        if telemetry_reason == 2:
            return "TARGETING_MATCH"
        if telemetry_reason == 3:
            return "SPLIT"
        # telemetry_reason == 0 — derive from the evaluator's coarse reason.
        # RULE_MATCH is the env-rule-matched path; treat as TARGETING_MATCH.
        if eval_reason == "RULE_MATCH" or eval_reason == "DEFAULT":
            return "TARGETING_MATCH"
        return "TARGETING_MATCH"

    def _evaluate_details(
        self,
        key: str,
        expected_type: str,
        contexts: Optional[Contexts] = None,
    ) -> EvaluationDetails[Any]:
        """Shared backbone for the public ``*_details`` getters.

        Returns an ``EvaluationDetails`` describing how the value was selected
        — STATIC / TARGETING_MATCH / SPLIT for successful evaluations, DEFAULT
        when the flag exists but no rule matched, and ERROR (with an
        ``error_code``) for FLAG_NOT_FOUND, TYPE_MISMATCH, and unexpected
        failures. Never raises — callers can rely on a return value in all
        cases.
        """
        try:
            self._wait_initialized()
            assert self._evaluator is not None
            merged = self._effective_contexts(contexts)
            result = self._evaluator.evaluate(key, merged)

            # Distinguish flag-not-in-store (FLAG_NOT_FOUND) from
            # flag-exists-but-no-rule-matched (DEFAULT). The evaluator returns
            # MISSING in both cases, so we use config_id as the discriminator:
            # it is only ``None`` when the store had no entry for this key.
            if result.reason == "MISSING":
                if result.config_id is None:
                    return EvaluationDetails(
                        value=None,
                        reason="ERROR",
                        error_code="FLAG_NOT_FOUND",
                        error_message=f"Flag '{key}' not found",
                        variant=self._build_variant("ERROR", None, None),
                        flag_metadata=self._build_flag_metadata(None, None, None, None, None),
                    )
                return EvaluationDetails(
                    value=None,
                    reason="DEFAULT",
                    variant=self._build_variant("DEFAULT", None, None),
                    flag_metadata=self._build_flag_metadata(
                        result.config_id, result.config_type, None, None, None
                    ),
                )

            if result.value is None:
                # Rule matched but produced no Value — treat as DEFAULT.
                return EvaluationDetails(
                    value=None,
                    reason="DEFAULT",
                    variant=self._build_variant("DEFAULT", None, None),
                    flag_metadata=self._build_flag_metadata(
                        result.config_id, result.config_type, None, None, None
                    ),
                )

            try:
                resolved = self._resolver.resolve(result.value, merged, config_key=key)
            except (QuonfigEnvVarNotSetError, QuonfigDecryptionError) as e:
                return EvaluationDetails(
                    value=None,
                    reason="ERROR",
                    error_code="GENERAL",
                    error_message=str(e),
                    variant=self._build_variant("ERROR", None, None),
                    flag_metadata=self._build_flag_metadata(
                        result.config_id, result.config_type, None, None, None
                    ),
                )
            except Exception as e:
                logger.warning("Error resolving value for key '%s': %s", key, e)
                return EvaluationDetails(
                    value=None,
                    reason="ERROR",
                    error_code="GENERAL",
                    error_message=str(e),
                    variant=self._build_variant("ERROR", None, None),
                    flag_metadata=self._build_flag_metadata(
                        result.config_id, result.config_type, None, None, None
                    ),
                )

            # Record telemetry for successful resolutions, mirroring _get so
            # the *_details path produces the same eval-summary aggregation as
            # the original getters.
            if self._telemetry is not None:
                result.resolved_value = resolved
                result.reportable_value = compute_reportable_value(result.value)
                try:
                    self._telemetry.record_evaluation(result)
                    if merged:
                        self._telemetry.record_context(merged)
                except Exception:
                    pass  # telemetry must never break a getter

            reason_str = self._telemetry_reason_to_string(result.telemetry_reason, result.reason)

            # Type coercion. We try to coerce the resolved value to the
            # caller's expected_type — surfacing TYPE_MISMATCH on failure
            # rather than letting a string sneak through a bool channel.
            coerced, ok = _coerce_value(resolved, expected_type)
            if not ok:
                return EvaluationDetails(
                    value=None,
                    reason="ERROR",
                    error_code="TYPE_MISMATCH",
                    error_message=(
                        f"Flag '{key}' could not be coerced to {expected_type}; "
                        f"got {type(resolved).__name__}"
                    ),
                    variant=self._build_variant("ERROR", None, None),
                    flag_metadata=self._build_flag_metadata(
                        result.config_id, result.config_type, None, None, None
                    ),
                )

            wvi = result.weighted_value_index if result.weighted_value_index >= 0 else None
            return EvaluationDetails(
                value=coerced,
                reason=reason_str,
                variant=self._build_variant(reason_str, result.row_index, wvi),
                flag_metadata=self._build_flag_metadata(
                    result.config_id,
                    result.config_type,
                    result.row_index,
                    wvi,
                    reason_str,
                ),
            )
        except Exception as e:  # noqa: BLE001 — *_details must never raise
            return EvaluationDetails(
                value=None,
                reason="ERROR",
                error_code="GENERAL",
                error_message=str(e),
                variant=self._build_variant("ERROR", None, None),
                flag_metadata=self._build_flag_metadata(None, None, None, None, None),
            )

    def _build_variant(
        self,
        reason: str,
        rule_index: Optional[int],
        weighted_value_index: Optional[int],
    ) -> str:
        """Build the variant string per the cross-SDK spec
        (``project/plans/openfeature-resolution-details.md`` §2)."""
        if reason == "STATIC":
            return "static"
        if reason == "TARGETING_MATCH":
            return f"targeting:{rule_index if rule_index is not None else 0}"
        if reason == "SPLIT":
            return f"split:{weighted_value_index if weighted_value_index is not None else 0}"
        return "default"

    def _build_flag_metadata(
        self,
        config_id: Optional[str],
        config_type: Optional[str],
        rule_index: Optional[int],
        weighted_value_index: Optional[int],
        reason: Optional[str],
    ) -> Dict[str, Any]:
        """Build the flag_metadata dict per the cross-SDK spec
        (``project/plans/openfeature-resolution-details.md`` §3) using
        Python's snake_case keys and the wire's snake_case config_type."""
        md: Dict[str, Any] = {}
        if config_id:
            md["config_id"] = config_id
        if config_type:
            md["config_type"] = config_type
        if self._environment:
            md["environment"] = self._environment
        if rule_index is not None and rule_index >= 0 and reason in ("TARGETING_MATCH", "SPLIT"):
            md["rule_index"] = rule_index
        if weighted_value_index is not None and reason == "SPLIT":
            md["weighted_value_index"] = weighted_value_index
        return md

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

    # ------------------------------------------------------------------
    # *_details API — value + reason + error_code, no exceptions
    # ------------------------------------------------------------------

    def get_bool_details(
        self,
        key: str,
        contexts: Optional[Contexts] = None,
    ) -> EvaluationDetails[bool]:
        """Resolve a bool flag and surface the evaluation reason.

        Never raises. Errors come back as ``reason="ERROR"`` with an
        ``error_code`` of ``"FLAG_NOT_FOUND"``, ``"TYPE_MISMATCH"``, or
        ``"GENERAL"``.
        """
        return self._evaluate_details(key, "bool", contexts)

    def get_string_details(
        self,
        key: str,
        contexts: Optional[Contexts] = None,
    ) -> EvaluationDetails[str]:
        """Resolve a string flag and surface the evaluation reason."""
        return self._evaluate_details(key, "string", contexts)

    def get_int_details(
        self,
        key: str,
        contexts: Optional[Contexts] = None,
    ) -> EvaluationDetails[int]:
        """Resolve an int flag and surface the evaluation reason."""
        return self._evaluate_details(key, "int", contexts)

    def get_float_details(
        self,
        key: str,
        contexts: Optional[Contexts] = None,
    ) -> EvaluationDetails[float]:
        """Resolve a float flag and surface the evaluation reason."""
        return self._evaluate_details(key, "float", contexts)

    def get_string_list_details(
        self,
        key: str,
        contexts: Optional[Contexts] = None,
    ) -> EvaluationDetails[List[str]]:
        """Resolve a string-list flag and surface the evaluation reason."""
        return self._evaluate_details(key, "string_list", contexts)

    def get_json_details(
        self,
        key: str,
        contexts: Optional[Contexts] = None,
    ) -> EvaluationDetails[Any]:
        """Resolve a JSON flag (or any flag, untyped) and surface the
        evaluation reason."""
        return self._evaluate_details(key, "any", contexts)

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
            raise ValueError("should_log: pass either `config_key` or `logger_path`, not both.")

        resolved_contexts = contexts

        if logger_path is not None:
            if not self._logger_key:
                raise ValueError(
                    "should_log(logger_path=...) requires the `logger_key` option on the "
                    'Quonfig constructor. Pass `logger_key="log-level.<your-app>"` or '
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
            raise ValueError("should_log requires either `config_key` or `logger_path`.")

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

    # ------------------------------------------------------------------
    # Layer 2 fallback poller — engage/disengage based on SSE state edges
    # ------------------------------------------------------------------

    def _handle_sse_state_change(self, state: str) -> None:
        """SSE state listener — drives the fallback poller's engage/disengage.

        States: ``connecting`` | ``connected`` | ``error`` | ``disconnected``.

        - ``connected``: clear pending engage, disengage poller (SSE recovered).
        - ``error`` BEFORE any successful connect: engage now (initial-fail).
        - ``error`` AFTER a successful connect: schedule a 2x-poll-interval
          grace timer; engage if the timer fires without reconnect.
        - ``connecting`` / ``disconnected``: no-op.
        """
        # Record the latest SSE state so `connection_state()` can derive the
        # customer-visible enum without re-reading transport internals.
        with self._health_lock:
            self._last_sse_state = state

        # Fan out to caller's observability callback first; the chaos harness
        # and OpenFeature provider both rely on this edge stream.
        if self._on_sse_connection_state_change is not None:
            try:
                self._on_sse_connection_state_change(state)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Quonfig: on_sse_connection_state_change threw: %s: %s",
                    type(e).__name__,
                    e,
                )

        if not self._fallback_poll_enabled or self._fallback_poller is None:
            return

        if state == "connected":
            with self._fallback_lock:
                self._sse_ever_connected = True
                if self._fallback_engage_timer is not None:
                    self._fallback_engage_timer.cancel()
                    self._fallback_engage_timer = None
            self._fallback_poller.disengage("sse-recovered")
            return

        if state == "error":
            with self._fallback_lock:
                ever_connected = self._sse_ever_connected
                pending = self._fallback_engage_timer is not None
            if not ever_connected:
                self._fallback_poller.engage("initial-sse-failure")
                return
            if not pending and not self._fallback_poller.is_active():
                # Connected → disconnected edge. Give the SSE library 2x
                # poll-interval to reconnect on its own before engaging
                # the fallback poller (matches sdk-node).
                grace_seconds = (self._fallback_poll_interval_ms / 1000.0) * 2.0

                def _engage_after_grace() -> None:
                    with self._fallback_lock:
                        self._fallback_engage_timer = None
                    if self._fallback_poller is not None:
                        self._fallback_poller.engage("sse-disconnected-grace-elapsed")

                timer = threading.Timer(grace_seconds, _engage_after_grace)
                timer.daemon = True
                with self._fallback_lock:
                    self._fallback_engage_timer = timer
                timer.start()
            return
        # connecting / disconnected → no-op

    def fallback_poller_active(self) -> bool:
        """``True`` when the Layer 2 HTTP fallback poller is currently
        scheduled. Mirrors sdk-node's ``fallbackPollerActive()`` — used by
        the chaos harness; the documented public ``connection_state()``
        accessor below is the customer-facing surface."""
        return self._fallback_poller is not None and self._fallback_poller.is_active()

    # ------------------------------------------------------------------
    # Customer-visible health primitives (qfg-47c2.15)
    #
    # WARNING: do NOT wire either of these into a Kubernetes liveness
    # probe. They are diagnostic, not pass/fail. A liveness probe based on
    # SDK freshness amplifies transient network blips into restart
    # cascades. See README.
    # ------------------------------------------------------------------

    def last_successful_refresh(self) -> Optional[datetime.datetime]:
        """Wall-clock time of the most recent installed config envelope.

        Returns ``None`` before the first install. Updated on every install
        source — datadir load, initial HTTP fetch, SSE event, fallback
        poll — via the shared ``_fire_on_config_update`` hook.
        """
        with self._health_lock:
            return self._last_successful_refresh

    def connection_state(self) -> str:
        """One of ``connected`` | ``disconnected`` | ``falling_back`` |
        ``initializing``.

        - ``falling_back``: Layer 2 HTTP fallback poller is active. Wins
          over the SSE state — even if SSE briefly reports `connected`
          before the poller disengages, an active poller is the truthful
          signal.
        - ``connected``: latest SSE state is ``connected``, or (datadir
          mode / pre-SSE) we have an installed envelope.
        - ``disconnected``: SSE reported ``error`` / ``disconnected`` and
          the fallback poller hasn't engaged yet (grace window).
        - ``initializing``: no SSE state has been observed AND no envelope
          has been installed — the SDK hasn't reached a known good state.
        """
        if self._fallback_poller is not None and self._fallback_poller.is_active():
            return "falling_back"
        with self._health_lock:
            last = self._last_sse_state
            last_refresh = self._last_successful_refresh
        if last == "connected":
            return "connected"
        if last in ("error", "disconnected"):
            return "disconnected"
        # `last` is None or "connecting" — no SSE established yet.
        if last_refresh is not None:
            # Datadir mode, or an HTTP install completed before SSE wired up.
            return "connected"
        return "initializing"

    def close(self) -> None:
        self._shutdown.set()
        # Cancel any pending fallback engage timer so the daemon doesn't fire
        # after close().
        with self._fallback_lock:
            if self._fallback_engage_timer is not None:
                self._fallback_engage_timer.cancel()
                self._fallback_engage_timer = None
        if self._fallback_poller is not None:
            try:
                self._fallback_poller.disengage("client-close")
            except Exception:
                pass
        if self._datadir_watcher is not None:
            try:
                self._datadir_watcher.close()
            except Exception:
                pass
            self._datadir_watcher = None
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
