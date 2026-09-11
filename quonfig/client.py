from __future__ import annotations

import contextlib
import datetime
import logging
import os
import threading
import uuid
import warnings
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from collections.abc import Callable

    from .bound_client import BoundQuonfig
    from .types import ConfigEnvelope, ConfigResponse

from ._fork import register_instance
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
        # qfg-o8zr: canonical init timeout is `init_timeout_ms` (milliseconds,
        # default 10_000). Mirrors sdk-ruby's `init_timeout_ms` (qfg-39za) and
        # sdk-node's `initTimeout` so all SDKs settle on ms. The legacy
        # `init_timeout` (seconds, float) and `initialization_timeout_sec`
        # (seconds, float) kwargs are kept as deprecated aliases for one
        # minor cycle — each emits a `DeprecationWarning` and is forwarded
        # as `value * 1000` into `_init_timeout_ms`. The canonical kwarg
        # wins when more than one is passed.
        init_timeout_ms: int = 10_000,
        init_timeout: Optional[float] = None,
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
        logger_key: Optional[str] = None,
        # Real-time SSE update channel (qfg-0xj3.1). Default ``True`` — the
        # historical (and only) behavior, so existing callers are unaffected.
        # Pass ``False`` on hosts where a long-lived stream is useless or
        # harmful (serverless / Lambda, short-lived batch jobs, environments
        # that block streaming responses). Mirrors sdk-node's ``enableSSE``.
        #
        #   * ``True`` + ``fallback_poll_enabled=True`` (default): SSE is the
        #     primary channel; the Layer 2 poller engages only when SSE fails.
        #   * ``False`` + ``fallback_poll_enabled=True``: no SSE client is
        #     constructed at all and the fallback poller becomes the PRIMARY
        #     update channel — engaged right after the initial fetch is kicked
        #     off, since the SSE state edges that normally engage it never
        #     arrive.
        #   * ``False`` + ``fallback_poll_enabled=False``: initial fetch only.
        #     Config then moves only via ``refresh()`` /
        #     ``update_if_staler_than()``.
        #
        # The chosen channel is logged once at init.
        enable_sse: bool = True,
        # Layer 2 HTTP fallback poller. Off-by-default-when-SSE-is-up: only
        # engages on initial-SSE-failure or after a sustained disconnect
        # (`fallback_poll_interval_ms` * 2 grace). Replaces the previous
        # always-on parallel poll (qfg-47c2.8).
        fallback_poll_enabled: bool = True,
        fallback_poll_interval_ms: int = 60000,
        # Per-URL config-fetch deadline (qfg-7h5d.1.8). Bounds a single per-leg
        # config-fetch attempt — the initial fetch and every fallback-poller
        # fetch alike — so a hung primary aborts fast and the secondary is
        # reached inside ``init_timeout_ms`` instead of being starved until it.
        # Additive and backward-compatible: the ~3s default already makes a hung
        # upstream fail over, so existing callers need not set this. Raise it
        # only if a healthy upstream legitimately takes longer than 3s to answer
        # a config fetch; lower it to fail over even faster. Mirrors sdk-go's
        # WithConfigFetchTimeout. Applies to the HTTP config path only — the
        # long-lived SSE stream keeps its own read deadline.
        config_fetch_timeout_ms: int = 3000,
        # Parallel-failover hedge timings (qfg-7h5d.1.14). The HTTP config-fetch
        # is now a hedge: fire the primary first, and only if it is slow past
        # ``hedge_delay_ms`` OR errors fast, ALSO fire the secondary in parallel
        # (without cancelling the primary). A healthy sub-second primary answers
        # well inside the delay, so the secondary stays a cold standby and a
        # healthy system adds zero secondary load. Both are additive and
        # backward-compatible; mirror sdk-go's WithConfigFetchHedgeDelay /
        # WithConfigFetchHedgeAbort.
        #
        #   * ``hedge_delay_ms`` (~2000): how long to wait for the primary before
        #     also firing the secondary in parallel.
        #   * ``config_fetch_hedge_abort_ms`` (~6000): the per-leg hard-abort
        #     deadline on the hedged path. It MUST exceed the longest healable
        #     primary latency (so a late-but-newer primary heals forward rather
        #     than aborting) and SHOULD be < ``init_timeout_ms`` (so the init-path
        #     heal leg is not clipped) — the client logs a warning at construction
        #     if ``init_timeout_ms <= config_fetch_hedge_abort_ms``.
        hedge_delay_ms: int = 2000,
        config_fetch_hedge_abort_ms: int = 6000,
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
        #
        # Tri-state (``None`` = unset). Default ON, gated only by the
        # presence of the tokens file: production servers do not have it, so
        # injection is a no-op there by construction. Precedence: this
        # explicit option (if not ``None``) wins, else ``QUONFIG_DEV_CONTEXT``
        # (``"true"``/``"false"``), else ``True``. Pass ``False`` (or
        # ``QUONFIG_DEV_CONTEXT=false``) to opt out.
        enable_quonfig_user_context: Optional[bool] = None,
    ) -> None:
        # Resolve configuration from params or env vars
        # `QUONFIG_BACKEND_SDK_KEY` is the canonical auto-load var shared by
        # every Quonfig SDK (go/node/ruby/java) and the `qfg run` CLI.
        self._sdk_key = sdk_key or os.environ.get("QUONFIG_BACKEND_SDK_KEY", "")
        self._environment = environment or os.environ.get("QUONFIG_ENVIRONMENT", "")
        self._datadir = datadir or os.environ.get("QUONFIG_DIR")

        # `QUONFIG_DOMAIN` governs both api_urls and telemetry_url defaults
        # so a single env var flips a service between prod and staging.
        # Explicit kwargs (`api_urls=`, `telemetry_url=`) remain the local-dev
        # escape hatch and supersede the env-derived defaults.
        domain = os.environ.get("QUONFIG_DOMAIN", "").strip() or _DEFAULT_DOMAIN
        default_api_urls, default_telemetry_url = _derive_defaults(domain)

        explicit_api_urls = bool(api_urls)
        if api_urls:
            self._api_urls = api_urls
        else:
            self._api_urls = default_api_urls

        self._telemetry_url = telemetry_url or default_telemetry_url
        # qfg-o8zr: canonical option is `init_timeout_ms` (milliseconds). When
        # the caller passes `init_timeout_ms` explicitly it wins. Otherwise
        # the legacy seconds-based aliases (in priority order
        # `init_timeout` → `initialization_timeout_sec`) are forwarded with
        # transparent `* 1000` multiplication and emit a DeprecationWarning.
        if init_timeout is not None:
            warnings.warn(
                "Quonfig: `init_timeout` (seconds) is deprecated; use "
                "`init_timeout_ms` (milliseconds) instead. Forwarding as "
                f"init_timeout_ms={int(float(init_timeout) * 1000)}.",
                DeprecationWarning,
                stacklevel=2,
            )
        if initialization_timeout_sec is not None:
            warnings.warn(
                "Quonfig: `initialization_timeout_sec` (seconds) is deprecated; "
                "use `init_timeout_ms` (milliseconds) instead. Forwarding as "
                f"init_timeout_ms={int(float(initialization_timeout_sec) * 1000)}.",
                DeprecationWarning,
                stacklevel=2,
            )
        if init_timeout_ms != 10_000:
            # Canonical kwarg was set explicitly — it wins outright.
            self._init_timeout_ms = int(init_timeout_ms)
        elif init_timeout is not None:
            self._init_timeout_ms = int(float(init_timeout) * 1000)
        elif initialization_timeout_sec is not None:
            self._init_timeout_ms = int(float(initialization_timeout_sec) * 1000)
        else:
            self._init_timeout_ms = int(init_timeout_ms)

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
        if enable_quonfig_user_context is not None:
            dev_context_enabled = enable_quonfig_user_context
        else:
            env_dev_context = os.environ.get("QUONFIG_DEV_CONTEXT")
            if env_dev_context == "true":
                dev_context_enabled = True
            elif env_dev_context == "false":
                dev_context_enabled = False
            else:
                dev_context_enabled = True
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
        # ``init()`` has been called at least once. Read by the at-fork hook:
        # a client that was constructed but never started has no background
        # components to rebuild, and must not be started behind the caller's
        # back in the child (qfg-lv4n.2).
        self._started = False

        # Will be set after init
        self._evaluator: Optional[Evaluator] = None
        self._resolver = Resolver(self._store)

        # Telemetry (optional).
        #
        # The SDK key is required, not optional: the telemetry endpoint
        # authenticates with it and the backend attributes every event to the
        # workspace it names. Without one there is nothing to attribute the
        # data to, so the reporter must not be constructed at all — otherwise
        # the collectors fill up and each flush POSTs `Authorization: Basic
        # base64("1:")`, an unauthenticated request that is rejected and then
        # retried with backoff. This is reachable on the open-source /
        # no-account path: a datadir-only client has no SDK key, so the gate
        # keys off the key rather than off the mode (qfg-j001). sdk-node's
        # `isTelemetryEnabled` is the reference implementation.
        #
        # Kept on `self` so the at-fork hook can build an identical reporter
        # with EMPTY buffers in a forked child (qfg-lv4n.2).
        self._collect_evaluation_summaries = collect_evaluation_summaries
        self._context_upload_mode = context_upload_mode
        self._telemetry = self._build_telemetry()

        # Transport: stand it up whenever the caller wired an HTTP source.
        # Explicit `api_urls=` without an sdk_key is the integration-suite
        # init-timeout pattern — point at a real-but-unreachable host to
        # exercise the timeout, not to fetch real configs. When only datadir
        # is configured, no transport is needed.
        # The request timeout is intentionally independent of
        # init_timeout_ms: capping it at a sub-second init-timeout would
        # surface a tiny init as a generic `requests.Timeout` from the
        # background fetch instead of letting `_wait_initialized` raise
        # `QuonfigInitTimeoutError`.
        self._config_fetch_timeout_ms = config_fetch_timeout_ms
        # Parallel-failover hedge timings (qfg-7h5d.1.14). The per-leg hedge
        # abort must sit BELOW init_timeout_ms so a late-but-newer primary's heal
        # leg is not clipped by the init budget. Warn (don't hard-fail) so a
        # deliberately short init timeout still works — just without init-path
        # heal-forward. Mirrors sdk-go's construction-time warning.
        self._hedge_delay_ms = hedge_delay_ms
        self._config_fetch_hedge_abort_ms = config_fetch_hedge_abort_ms
        if self._init_timeout_ms > 0 and self._init_timeout_ms <= config_fetch_hedge_abort_ms:
            logger.warning(
                "Quonfig: init_timeout_ms (%dms) <= config_fetch_hedge_abort_ms "
                "(%dms); the init-path heal-forward leg may be clipped. Raise "
                "init_timeout_ms or lower config_fetch_hedge_abort_ms.",
                self._init_timeout_ms,
                config_fetch_hedge_abort_ms,
            )
        self._transport: Optional[Transport] = None
        if (not self._datadir and self._sdk_key) or (explicit_api_urls and not self._datadir):
            self._transport = self._build_transport()

            # A single explicit `api_urls` entry disables automatic failover:
            # the default (and every QUONFIG_DOMAIN-derived) list carries both a
            # primary and a secondary leg, and the SDK hedges/fails over between
            # them. An explicit `api_urls=` replaces that list wholesale, so a
            # one-entry override silently drops the secondary. Warn once at init
            # pointing the caller at the fix (mirrors sdk-go). See qfg-41nh.26.
            if explicit_api_urls and len(self._api_urls) < 2:
                logger.warning(
                    "Quonfig: explicit api_urls disables automatic failover to "
                    "the secondary; pass both primary and secondary URLs to keep it"
                )

        # Canonical-ordering observability (qfg-7h5d.1.8). ``_resolved_from_index``
        # is the api_urls index of the leg that produced the config we currently
        # hold (set only on the HTTP install path, never by SSE); -1 until the
        # first successful HTTP install. ``_sse_stream_index`` is always 0 — SSE
        # is pinned to the primary leg and does not fail over — so
        # ``sse_failed_over_to_secondary()`` is an asserted invariant (f05).
        self._resolved_from_index = -1
        self._sse_stream_index = 0

        # qfg-pinh: an environment pin (`environment=` / QUONFIG_ENVIRONMENT)
        # only takes effect in datadir mode, where the loader uses it to pick
        # the env. In SDK-key delivery mode the server's `meta.environment` is
        # authoritative and the pin is ignored — warn once at construction so a
        # misconfigured pin is visible rather than silently dead.
        if self._environment and self._transport is not None and not self._datadir:
            logger.warning(
                "Quonfig: environment '%s' was set but the client is in delivery "
                "(SDK-key) mode; the active environment is determined by the SDK "
                "key, so this setting is ignored (it applies only when loading "
                "from a local data dir).",
                self._environment,
            )

        # Layer 2 fallback poller state. The poller itself is only constructed
        # once a transport exists; the state vars below drive the engage/
        # disengage decision when SSE state changes arrive.
        self._enable_sse = enable_sse
        self._fallback_poll_enabled = fallback_poll_enabled
        self._fallback_poll_interval_ms = fallback_poll_interval_ms
        self._fallback_poller: Optional[FallbackPoller] = None
        # The live SSE client, when one is running. Stays ``None`` in datadir
        # mode and whenever ``enable_sse=False`` (qfg-0xj3.1) — the SSE client
        # is then never even constructed.
        self._sse: Optional[Any] = None  # quonfig.sse.SSEClient
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

        # Staleness-triggered refresh coalescing (qfg-0xj3.2).
        # ``update_if_staler_than`` is called per-request on serverless hosts,
        # so a slow or down upstream must never stack worker threads: at most
        # one staleness-triggered refresh is in flight at a time. The flag is
        # set under ``_staleness_lock`` before the thread starts and cleared in
        # the worker's ``finally``.
        self._staleness_lock = threading.Lock()
        self._staleness_refresh_in_flight = False
        if self._transport is not None and self._fallback_poll_enabled:
            self._fallback_poller = self._build_fallback_poller()

        # Fork safety (qfg-lv4n.2). The child-only ``os.register_at_fork``
        # handler in ``quonfig._fork`` only DROPS inherited state and sets
        # ``_needs_rebuild_after_fork``; the re-initialization itself happens
        # lazily, on the child's first use of the client, through
        # ``_ensure_rebuilt_after_fork``. ``_fork_lock`` serializes that
        # one-shot rebuild; the other two are set by the drop handler and read
        # by the rebuild.
        self._needs_rebuild_after_fork = False
        self._fork_lock = threading.RLock()
        self._fork_had_transport = self._transport is not None
        self._fork_stream_url_override: Optional[str] = None

        # Track this instance so the at-fork hook can find it. Registered LAST
        # so a construction that raised is never handed to the hook. Weakly
        # held — this does not keep the client alive.
        register_instance(self)

    # ------------------------------------------------------------------
    # Component factories
    #
    # Shared by ``__init__`` and the at-fork child rebuild so the two can't
    # drift: a forked child must get exactly what a fresh instance gets.
    # ------------------------------------------------------------------

    def _build_telemetry(self) -> Optional[Any]:
        """Build the telemetry reporter, or ``None`` when telemetry is off.

        Buffers start empty by construction, which is what makes this safe to
        call in a forked child: the parent's buffered window is the parent's
        to flush.

        The SDK instance hash is minted HERE, not once in ``__init__``
        (qfg-58bo). It identifies one live SDK instance in app-quonfig's
        last-seen / Debugger view, which groups by
        ``(sdk_key_id, sdk_instance_hash)`` — an empty hash collapsed every
        Python process on one SDK key into a single row. Minting it at
        reporter-build time means the lazy post-fork rebuild gives each forked
        child its OWN hash (Jeff's decision 2026-09-11 via qfg-xcym, matching
        Reforge and sdk-node/sdk-ruby's one-per-client behavior), so a Gunicorn
        cluster shows one row per worker.
        """
        if not self._sdk_key:
            return None
        if not (self._collect_evaluation_summaries or self._context_upload_mode != "none"):
            return None
        try:
            from .telemetry import TelemetryReporter

            return TelemetryReporter(
                telemetry_url=self._telemetry_url,
                sdk_key=self._sdk_key,
                instance_hash=str(uuid.uuid4()),
                collect_evaluation_summaries=self._collect_evaluation_summaries,
                context_upload_mode=self._context_upload_mode,
            )
        except Exception:  # noqa: BLE001 — telemetry is optional
            return None

    def _build_fallback_poller(self) -> FallbackPoller:
        assert self._transport is not None
        return FallbackPoller(
            transport=self._transport,
            store=self._store,
            interval_seconds=self._fallback_poll_interval_ms / 1000.0,
            shutdown_event=self._shutdown,
            on_config_update=self._fire_on_config_update,
            install=lambda env: self._install_network_envelope(env, from_http=True),
            # Hedged refresh (qfg-7h5d.1.14): each poll tick drives a full
            # parallel-failover hedge so an established client heals forward
            # to a newer leg on the poll loop too, not just at init.
            refresh=lambda: self._fetch_and_install_hedged(initial=False),
        )

    def _build_transport(self) -> Transport:
        return Transport(
            api_urls=self._api_urls,
            sdk_key=self._sdk_key,
            timeout=self._config_fetch_timeout_ms / 1000.0,
            hedge_delay=self._hedge_delay_ms / 1000.0,
            hedge_abort=self._config_fetch_hedge_abort_ms / 1000.0,
        )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def init(self) -> "Quonfig":
        """
        Kick off the first config load.

        For datadir mode the load is synchronous and ``init()`` returns
        once the store is populated. For HTTP mode the fetch runs on a
        background thread so a tiny ``init_timeout_ms`` can be enforced
        lazily by ``_wait_initialized`` on the first getter call —
        raising ``QuonfigInitTimeoutError`` when ``on_init_failure``
        is ``"raise"``.
        """
        # In a forked child the at-fork handler dropped the transport, poller
        # and telemetry reporter, so a bare ``init()`` here would stand up an
        # empty client. Route through the rebuild path, which rebuilds those
        # components and calls back into ``init()`` with the flag cleared.
        if self._needs_rebuild_after_fork:
            self._ensure_rebuilt_after_fork()
            return self

        # A client that was constructed but never ``init()``ed before a fork is
        # deliberately NOT flagged for rebuild — the child must not start it
        # behind the caller's back. But the at-fork handler still dropped its
        # transport, telemetry reporter and poller (they may hold the parent's
        # pooled sockets), so a bare ``init()`` here would fall through to the
        # "no data source configured" branch below and latch an empty store
        # forever. Rebuild them first: this is the construct-in-master /
        # ``init()``-in-``post_fork`` pattern (qfg-lv4n.2). A client that was
        # CLOSED before the fork is excluded: closed clients stay closed in the
        # child, and standing a transport back up here would start a fetch and
        # a poll thread on one.
        if self._transport is None and self._fork_had_transport and not self._shutdown.is_set():
            self._build_components_after_fork()

        self._started = True
        if self._datadir:
            self._load_from_datadir()
        elif self._transport:
            self._load_from_api()
        else:
            # No data source configured — mark initialized with empty store
            self._finish_init()

        return self

    # ------------------------------------------------------------------
    # Fork safety (qfg-lv4n.2)
    # ------------------------------------------------------------------

    def _drop_inherited_state_after_fork_in_child(self) -> None:
        """Drop inherited state — IN THE CHILD ONLY, and cheaply.

        Called once per live instance from the ``after_in_child`` handler in
        ``quonfig._fork``. **The parent is never touched**, before or after the
        syscall. This ports Reforge sdk-python 1.2.2's singleton reset (#26) to
        Quonfig's instance-based API; see that module's docstring for the
        survey and the accepted differences.

        This handler does NOTHING that can block, allocate a thread, take an
        inherited lock, or touch the network. That is the whole design: an
        ``after_in_child`` handler runs on EVERY fork in the process, including
        ``multiprocessing`` workers that never use the SDK and the pre-exec
        child of a ``subprocess`` spawn that passes ``preexec_fn``. Rebuilding
        eagerly here charged all of them a full fetch plus a set of threads,
        and on macOS killed them outright: ``requests`` resolves proxy settings
        through ``_scproxy``, which enters the Objective-C runtime, and libobjc
        deliberately aborts a child that does that after a fork from a
        multi-threaded parent. Reforge is lazy for the same reason — its hook
        only drops the singleton, and the next ``get_sdk()`` builds a new one.

        Three rules, in order:

        1. **Nothing inherited is joined or closed.** The parent's threads do
           not exist in this process (``threading``'s own after-fork reinit has
           already marked them stopped), so joining one would be a join on a
           thread that never runs. Its sockets still belong to the parent —
           closing an inherited TLS connection would write ``close_notify``
           onto the parent's live connection. Every inherited handle is simply
           dropped, including the pending ``threading.Timer`` (``cancel()``
           would touch the timer's inherited ``Event``).
        2. **Every lock and event the client owns is replaced.** A
           ``threading.Lock`` / ``RLock`` / ``Event`` held by a non-forking
           thread at fork time can never be released here, so the first caller
           to touch it would wedge forever. That is exactly why Reforge's
           ``_reset_singleton_after_fork`` swaps its module ``_ReadWriteLock``
           for a fresh one. Only ``is_set()`` is read off the inherited events
           — it takes no lock; ``set()`` would.
        3. **The client is left looking newly constructed**, with an EMPTY
           store, and flagged for rebuild. The child does not evaluate from the
           parent's snapshot: on its first use it runs the normal ``init()``
           path and fetches its own config (Jeff's call, 2026-09-10 — Reforge
           parity, where the child's next ``get_sdk()`` is a brand-new SDK).
           Starting at generation zero also means the child's first fetch is
           accepted by the reject-older guard rather than counted as a
           ``guardRejected``, so a forked worker adds no phantom rejections to
           the ``sdk_failover`` signal.

        A client that was closed before the fork stays closed, and one that was
        constructed but never ``init()``ed is not started behind the caller's
        back; neither is flagged for rebuild, and neither has its store touched.
        The caller can still start the never-started one itself by calling
        ``init()`` in the child — see ``init()``, which rebuilds the components
        this dropped (the construct-in-master / ``init()``-in-``post_fork``
        shape). A client that is already PENDING from an earlier fork is NOT
        one of those two cases: it stays pending across a re-fork, or the
        grandchild would never rebuild at all (qfg-lv4n.2).
        """
        was_closed = self._shutdown.is_set()
        was_initialized = self._initialized.is_set()
        was_started = self._started
        # Already flagged by an EARLIER fork and not used since: this process is
        # itself a forked child that has not rebuilt yet. It looks exactly like
        # a never-started client (``_started`` is False, no components) but it
        # is a PENDING one, and re-forking it must keep it pending (qfg-lv4n.2).
        pending = self._needs_rebuild_after_fork
        old_transport = self._transport

        # (2) Fresh locks and events. Never call .set() on an inherited event.
        self._fork_lock = threading.RLock()
        self._fallback_lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._staleness_lock = threading.Lock()
        self._staleness_refresh_in_flight = False
        self._shutdown = threading.Event()
        if was_closed:
            self._shutdown.set()

        # (1) Drop every inherited background component — no join, no close.
        self._sse = None
        self._fallback_poller = None
        self._fallback_engage_timer = None
        self._datadir_watcher = None
        self._telemetry = None
        self._transport = None

        # Inherited health state belongs to the parent's connection. Keeping it
        # is the Cheddar Up failure mode: a worker answering "connected" from
        # state it inherited while receiving nothing (epic qfg-lv4n).
        self._last_successful_refresh = None
        self._last_sse_state = None
        self._sse_ever_connected = False
        self._sse_stream_index = 0
        self._resolved_from_index = -1

        if was_closed or not (was_started or pending):
            # Nothing will be rebuilt, so the store is left exactly as it was
            # (a closed client still answers from what it holds; a client that
            # was never started holds nothing anyway). Its lock is replaced
            # like every other, and so is the initialized event — an inherited
            # ``Event``'s internal lock is not reinitialized by ``threading``'s
            # own after-fork hook, so ``wait()`` on one could wedge the child.
            self._store._lock = threading.RLock()
            self._initialized = threading.Event()
            if was_initialized:
                self._initialized.set()
            self._needs_rebuild_after_fork = False
            return

        # (3) Empty store, uninitialized, flagged for rebuild.
        self._store = ConfigStore()
        self._resolver = Resolver(self._store)
        self._evaluator = None
        self._initialized = threading.Event()
        self._init_error = None
        self._started = False
        if not pending:
            # Captured from the transport this fork is dropping. On a re-fork of
            # a still-pending child there is no transport left to read them off
            # — they were captured at the FIRST fork and must survive, or the
            # grandchild rebuilds without a transport and goes dark.
            self._fork_had_transport = old_transport is not None
            # Private SSE stream-URL test seam: configuration the chaos rig sets
            # after construction, not inherited runtime state.
            self._fork_stream_url_override = getattr(
                old_transport, "_Transport__test_stream_url_override", None
            )
        self._needs_rebuild_after_fork = True

    def _build_components_after_fork(self) -> None:
        """Re-create the background components the at-fork handler dropped.

        Shared by the lazy rebuild (``_rebuild_in_child``) and by an explicit
        ``init()`` in a child on a client that was never started in the parent,
        so the two cannot drift. Everything is built FRESH — in particular a
        fresh ``requests.Session`` per component, so a child never writes on a
        socket the parent pooled, and a telemetry reporter with EMPTY buffers,
        because the parent's buffered window is the parent's to flush.
        """
        if self._fork_had_transport:
            self._transport = self._build_transport()
            if self._fork_stream_url_override:
                setattr(
                    self._transport,
                    "_Transport__test_stream_url_override",
                    self._fork_stream_url_override,
                )

        self._telemetry = self._build_telemetry()

        if self._transport is not None and self._fallback_poll_enabled:
            self._fallback_poller = self._build_fallback_poller()

    def _ensure_rebuilt_after_fork(self) -> None:
        """Re-initialize this client if it came out of a fork — the LAZY half
        of the fork handling, and a no-op (one boolean read) otherwise.

        Called from every public entry point that uses the client. The flag is
        the only trigger: a pid comparison would also fire for clients that
        were constructed after the fork, and deciding whether the SDK should
        carry a pid-mismatch safety net at all is a separate open question
        (qfg-lv4n.3).
        """
        if not self._needs_rebuild_after_fork:
            return
        with self._fork_lock:
            if not self._needs_rebuild_after_fork:
                return
            # Cleared BEFORE the rebuild so a re-entrant call from inside
            # ``init()`` cannot recurse, and a rebuild that raises is not
            # retried on every subsequent call.
            self._needs_rebuild_after_fork = False
            self._rebuild_in_child()

    def _rebuild_in_child(self) -> None:
        """Stand the client back up in a forked child: fresh transport (fresh
        ``requests.Session``, so a child never reuses a parent's pooled
        socket), fresh telemetry reporter with EMPTY buffers (the parent
        flushes its own window — dd-trace-rb discards inherited buffers in the
        child for the same reason), fresh fallback poller, and a fresh update
        channel, by running the normal ``init()`` path.

        Datadir mode re-reads from disk here, exactly as a fresh client would.
        """
        self._build_components_after_fork()

        try:
            self.init()
        except Exception as e:  # noqa: BLE001 — a lazy rebuild must not break the caller
            logger.error(
                "[quonfig] re-initializing the SDK client after fork failed in pid %d: %s: %s",
                os.getpid(),
                type(e).__name__,
                e,
            )

        if self._shutdown.is_set():
            # ``close()`` landed while this rebuild was in flight. It takes
            # ``_fork_lock`` too, so it cannot interleave with the body above —
            # but a teardown that ran BEFORE we got the lock walked past
            # components that did not exist yet, and starting them now would
            # leave live threads on a closed client (a ``quonfig-telemetry``
            # reporter POSTing for the rest of the process's life). Tear the
            # new ones down again. ``_fork_lock`` is an RLock, so re-entering
            # ``close()`` on this thread is safe.
            logger.debug(
                "[quonfig] client closed during the post-fork rebuild in pid %d; "
                "tearing the rebuilt components down again",
                os.getpid(),
            )
            self.close()
            return

        components = []
        if self._transport is not None:
            components.append("transport")
        if self._sse is not None:
            components.append("SSE stream")
        if self._fallback_poller is not None:
            components.append("fallback poller")
        if self._telemetry is not None:
            components.append("telemetry")
        if self._datadir_watcher is not None:
            components.append("datadir watcher")
        logger.info(
            "[quonfig] fork detected: re-initialized the SDK client on first use in child "
            "pid %d (%s); the parent process is untouched",
            os.getpid(),
            ", ".join(components) if components else "no background components",
        )

    def _record_successful_refresh(self) -> None:
        """Stamp 'now' as the most recent successful refresh — the last moment
        the SDK confirmed its config source reachable and its held config
        current (qfg-41nh.11).

        Called by ``_fire_on_config_update`` for installs, and directly at the
        successful-but-NOT-installed sites: a 304 Not Modified, a 200 the
        reject-older guard dropped as equal-or-older, or a guard-no-op'd SSE
        message. Transport errors never call it.
        """
        with self._health_lock:
            self._last_successful_refresh = datetime.datetime.now(datetime.timezone.utc)

    def _fire_on_config_update(self) -> None:
        """Invoke the user's on_config_update callback, swallowing any
        exceptions and logging them. Mirrors sdk-node's `invokeOnConfigUpdate`
        — chaos scenario 10 requires the SDK supervisor to catch user-callback
        throws so the rest of the SDK keeps running.

        Also stamps `last_successful_refresh()`: every install path funnels
        through here (datadir, initial fetch, SSE event, fallback poll), so an
        install always advances the liveness stamp. The successful-but-NOT-
        installed paths (a 304, a guard-rejected payload) call
        ``_record_successful_refresh`` directly instead, so an install never
        double-stamps (qfg-41nh.11).
        """
        self._record_successful_refresh()
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

    def _install_network_envelope(
        self,
        envelope: "ConfigEnvelope",
        *,
        from_http: bool,
        source_index: Optional[int] = None,
    ) -> bool:
        """Install an envelope arriving on a network path under the reject-older
        guard (qfg-7h5d.1.8).

        Every network install path funnels through here — the initial HTTP
        fetch, the hedged failover/poll fetch, and SSE snapshot/update — so the
        canonical-ordering rule is applied uniformly: an established client
        installs only if ``incoming.generation`` advances the held generation.
        Returns ``True`` when the install was accepted.

        On an accepted HTTP install, record which leg produced the held config
        so ``resolved_from()`` reflects it; SSE installs leave it untouched
        because the stream is pinned to the primary leg.

        The hedge runs two legs concurrently, so the produced-by leg is passed
        in explicitly via ``source_index`` (a shared ``transport.last_fetch_index``
        scalar cannot identify which of two in-flight legs produced this result).
        The store update + resolved-from stamp are taken together under the
        store's lock (via the ``on_installed`` hook, which the store invokes
        while holding it) so a reader cannot observe a new generation paired
        with a stale resolved-from index. Legacy callers that omit
        ``source_index`` fall back to ``transport.last_fetch_index``
        (sequential-path behavior).
        """

        def _stamp_resolved_from() -> None:
            # Runs UNDER the store lock, only on an accepted install.
            if not from_http or self._transport is None:
                return
            if source_index is not None:
                self._resolved_from_index = source_index
            else:
                self._resolved_from_index = self._transport.last_fetch_index

        accepted = self._store.update(envelope, guard=True, on_installed=_stamp_resolved_from)

        # Failover observability (qfg-41nh.18). Recorded OUTSIDE the store lock —
        # the on_installed hook holds up every store reader and must stay cheap.
        # On an ACCEPTED HTTP install, record which leg (primary/secondary)
        # served the config now held; an equal-or-older payload dropped by the
        # reject-older guard on ANY network path (HTTP config-fetch OR SSE
        # message) counts as a guard rejection. SSE/datadir installs don't move
        # resolved_from, so they're not counted there.
        if self._telemetry is not None:
            if accepted:
                if from_http and self._transport is not None:
                    idx = (
                        source_index
                        if source_index is not None
                        else self._transport.last_fetch_index
                    )
                    self._telemetry.record_resolved_from(idx)
            else:
                self._telemetry.record_guard_rejected()

        return accepted

    def _fetch_and_install_hedged(self, *, initial: bool) -> bool:
        """Drive ONE hedged config-fetch cycle and install whatever arrives
        through the reject-older guard. Mirrors sdk-go's ``fetchAndInstall``.

        The hedge fires the primary first and, only if it is slow or errors,
        fires the secondary in parallel; results are installed as they arrive so
        watermark-max falls out (higher generation wins, a late older payload
        never regresses, a late newer payload heals forward). Returns ``True`` if
        at least one envelope was installed this cycle.

        Concurrent hedge cycles (a manual refresh racing the fallback poller,
        say) are SAFE and are NOT coalesced: each leg uses its own per-URL ETag
        slot, every install is serialized through the store lock + the
        reject-older guard (so an equal-or-older payload is a no-op and the
        install count can't double), and each leg is bounded by hedge_abort.
        Coalescing here would make a manual ``refresh()`` silently no-op whenever
        a background fetch is in flight, which violates the refresh contract.
        """
        if self._transport is None:
            return False
        installed_once = False
        fired = 0
        for lr in self._transport.fetch_hedged():
            fired += 1
            if lr.error is not None:
                continue
            if lr.not_changed:
                # 304 Not Modified: the leg answered and confirmed the held
                # config is current — a successful refresh with nothing to
                # install, so liveness still advances (qfg-41nh.11).
                self._record_successful_refresh()
                continue
            if lr.envelope is None:
                continue
            accepted = self._install_network_envelope(
                lr.envelope, from_http=True, source_index=lr.source_index
            )
            if accepted:
                self._fire_on_config_update()
                if not installed_once:
                    installed_once = True
                    if initial:
                        self._finish_init()
            else:
                # 200 dropped by the reject-older guard (equal-or-older
                # payload): the fetch itself succeeded, so liveness advances —
                # only the install was a no-op (qfg-41nh.11).
                self._record_successful_refresh()
        # Failover observability (qfg-41nh.18): if more than the primary leg
        # fired, the hedge fired its secondary leg this cycle (the primary was
        # slow or errored). Recorded once per cycle regardless of which leg's
        # payload won the guard. Mirrors sdk-go's `fired > 1` check.
        if fired > 1 and self._telemetry is not None:
            self._telemetry.record_hedge_fired()
        return installed_once

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
        ``init_timeout_ms`` actually surface as ``QuonfigInitTimeoutError``
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
                # Parallel-failover hedge (qfg-7h5d.1.14): fire the primary first
                # and, only if it is slow past hedge_delay OR errors fast, also
                # fire the secondary in parallel. Readiness latches on the first
                # accepted install (inside _fetch_and_install_hedged); a late
                # newer leg heals forward, a late older leg is rejected. On the
                # both-legs-fail path nothing installs and the finally below
                # latches init so on_init_failure applies — preserving the
                # init-failure contract for the both-fail case.
                self._fetch_and_install_hedged(initial=True)
            except Exception as e:
                # The update channel is started regardless (see below) so a
                # client that missed its initial fetch can still heal forward.
                logger.warning(
                    "Initial fetch failed: %s — starting the %s update channel anyway",
                    e,
                    "SSE" if self._enable_sse else "HTTP poll",
                )
            finally:
                self._finish_init()

        threading.Thread(target=_initial_fetch, daemon=True, name="quonfig-init").start()

        # Announce the chosen update channel once, before wiring it up, so a
        # deployer can see from the logs which one is live. Mirrors sdk-node's
        # `logBootMode` (qfg-47c2.7 / qfg-0xj3.1).
        self._log_boot_mode()

        if self._enable_sse:
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
                install=lambda env: self._install_network_envelope(env, from_http=False),
                record_refresh=self._record_successful_refresh,
            )
            self._sse.start()
        elif self._fallback_poller is not None:
            # SSE is off, so the Layer 2 poller is the ONLY update channel —
            # engage it now rather than waiting on `_handle_sse_state_change`,
            # which can never fire without an SSE client (qfg-0xj3.1). Mirrors
            # sdk-node's `engageFallbackPoller("sse-disabled")`. The poller
            # fetches immediately on engage; that first tick racing the initial
            # fetch is harmless (per-leg ETags + the reject-older guard).
            self._fallback_poller.engage("sse-disabled")

        # Start telemetry
        if self._telemetry is not None:
            self._telemetry.start()

    def _log_boot_mode(self) -> None:
        """Log which channel will actually deliver config updates.

        Message shapes mirror sdk-node's ``logBootMode`` so the four modes read
        identically across SDKs.
        """
        if self._enable_sse and self._fallback_poll_enabled:
            logger.info(
                "[quonfig] update channel: SSE (real-time) with HTTP fallback poll every "
                "%dms when SSE is unavailable",
                self._fallback_poll_interval_ms,
            )
        elif self._enable_sse:
            logger.info(
                "[quonfig] update channel: SSE only (fallback poll disabled — set "
                "fallback_poll_enabled=True for HTTP fallback during SSE outages)"
            )
        elif self._fallback_poll_enabled:
            logger.info(
                "[quonfig] update channel: HTTP polling only (every %dms; SSE disabled)",
                self._fallback_poll_interval_ms,
            )
        else:
            logger.info(
                "[quonfig] update channel: NONE (both SSE and fallback poll are disabled — "
                "config will not refresh after init; call refresh() or "
                "update_if_staler_than() to pull a new snapshot)"
            )

    def _finish_init(self) -> None:
        self._evaluator = Evaluator(self._store, self._environment)
        self._initialized.set()

    def _wait_initialized(self) -> None:
        self._ensure_rebuilt_after_fork()
        if not self._initialized.is_set():
            # threading.Event.wait takes seconds; init timeout is stored in ms.
            ok = self._initialized.wait(timeout=self._init_timeout_ms / 1000.0)
            if not ok:
                if self._on_init_failure == "raise":
                    raise QuonfigInitTimeoutError(
                        f"Quonfig did not initialize within {self._init_timeout_ms}ms"
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
        self._ensure_rebuilt_after_fork()
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
        """Keys of every config currently loaded.

        A content reader, not a diagnostic: in a forked child it triggers the
        rebuild AND waits for that child's own first fetch, so it never hands
        back the empty pre-fetch store (qfg-lv4n.2).
        """
        self._wait_initialized()
        return self._store.keys()

    def raw_config(self, key: str) -> "Optional[ConfigResponse]":
        """Return the raw ``ConfigResponse`` envelope for ``key``, pre-unwrap.

        This is the loaded config entry exactly as it sits in the store —
        before any rule evaluation or value unwrapping. Returns ``None`` if
        no config with that key is loaded. Mirrors sdk-node's ``rawConfig``;
        intended for advanced usage / tooling that needs the on-the-wire
        shape rather than a resolved value.

        Like ``keys()``, a content reader: in a forked child it waits for that
        child's own first fetch rather than answering from the empty pre-fetch
        store (qfg-lv4n.2).
        """
        self._wait_initialized()
        return self._store.get(key)

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

    def refresh(self) -> bool:
        """Manually drive one parallel-failover hedge config-fetch cycle
        (qfg-7h5d.1.14). Mirrors sdk-go's ``Refresh``.

        Fires the primary first and, only if it is slow past ``hedge_delay_ms``
        OR errors fast, also fires the secondary in parallel. Returns ``True``
        once the FIRST leg's envelope has been installed (or ``False`` if every
        fired leg failed / 304'd with nothing newer). A late-but-newer leg keeps
        draining on a detached daemon thread (bounded by ``hedge_abort``) and
        heals the client forward after this call has already returned.

        This is NOT coalesced against a background fetch in flight — a manual
        refresh always actually fetches. Concurrent installs are safe: per-leg
        ETag isolation + the reject-older guard keep an equal/older payload a
        no-op and the install count from doubling.
        """
        self._ensure_rebuilt_after_fork()
        if self._transport is None:
            return False

        installed_first = threading.Event()
        result: Dict[str, bool] = {"installed": False}

        def _drive() -> None:
            transport = self._transport
            if transport is None:
                installed_first.set()
                return
            try:
                fired = 0
                for lr in transport.fetch_hedged():
                    fired += 1
                    if lr.error is not None:
                        continue
                    if lr.not_changed:
                        # 304 Not Modified: fetch succeeded, held config
                        # confirmed current — advance liveness (qfg-41nh.11).
                        self._record_successful_refresh()
                        continue
                    if lr.envelope is None:
                        continue
                    accepted = self._install_network_envelope(
                        lr.envelope, from_http=True, source_index=lr.source_index
                    )
                    if accepted:
                        self._fire_on_config_update()
                        if not result["installed"]:
                            result["installed"] = True
                            installed_first.set()
                    else:
                        # Guard-rejected 200 (equal-or-older): the fetch
                        # succeeded, only the install was a no-op — advance
                        # liveness (qfg-41nh.11).
                        self._record_successful_refresh()
                # Failover observability (qfg-41nh.18): a fired secondary leg
                # this cycle means the hedge fired (primary slow/errored).
                if fired > 1 and self._telemetry is not None:
                    self._telemetry.record_hedge_fired()
            finally:
                # Unblock the caller even if nothing installed (all legs failed /
                # 304'd) so refresh() always returns rather than hanging.
                installed_first.set()

        threading.Thread(target=_drive, daemon=True, name="quonfig-refresh").start()
        # Return as soon as the first leg installs (or every fired leg settles
        # with nothing new). Bounded by hedge_abort so a hung leg can't wedge the
        # caller past the per-leg deadline.
        installed_first.wait(timeout=self._config_fetch_hedge_abort_ms / 1000.0)
        return result["installed"]

    def update_if_staler_than(self, max_age_ms: int) -> bool:
        """Refresh the config in the background if it is older than
        ``max_age_ms`` — stale-while-revalidate. NON-BLOCKING (qfg-0xj3.2).

        Built for serverless hosts (AWS Lambda, Vercel) where the background
        SSE stream and fallback poller are frozen between invocations, so the
        held config can be arbitrarily stale at the top of a request. Call this
        first thing in a handler to bound staleness without paying a network
        round-trip on the request path. Mirrors sdk-node's
        ``updateIfStalerThan``.

        Behavior:

        * Fresh (``now - last_successful_refresh() <= max_age_ms``) — returns
          ``False`` having done nothing but take one lock and read the clock.
        * Stale, or never refreshed — fires ONE hedged refresh cycle on a
          daemon thread and returns ``True`` **immediately**. The caller never
          waits on the network.
        * Already refreshing — returns ``False``. Staleness-triggered
          refreshes are coalesced: at most one is ever in flight, so calling
          this per-request against a slow or down upstream cannot stack
          threads.
        * No transport (datadir mode), or the client is closed — ``False``.

        ``True`` means "a refresh was **triggered**", not "a refresh
        completed". The new envelope installs asynchronously, so the value the
        current request reads is usually still the previous one; the next
        request sees the fresher config.

        Lambda freeze semantics: the worker thread may not finish before the
        execution environment is frozen. It resumes and completes on the next
        thaw, and installing then is safe — every network install goes through
        the reject-older guard, so a late payload that is equal to or older
        than what the client now holds is dropped rather than regressing it.

        Exceptions raised by the refresh are caught and logged on the worker
        thread; they are never raised into the caller, which has typically
        already returned its response.
        """
        self._ensure_rebuilt_after_fork()
        if self._transport is None:
            return False
        if self._shutdown.is_set():
            return False

        with self._health_lock:
            last = self._last_successful_refresh
        if last is not None:
            age_ms = (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() * 1000.0
            if age_ms <= max_age_ms:
                return False

        with self._staleness_lock:
            if self._staleness_refresh_in_flight:
                return False
            self._staleness_refresh_in_flight = True

        def _refresh_in_background() -> None:
            try:
                self._fetch_and_install_hedged(initial=False)
            except Exception as e:  # noqa: BLE001 — never escape into the caller
                logger.warning(
                    "Quonfig: staleness-triggered refresh failed: %s: %s",
                    type(e).__name__,
                    e,
                )
            finally:
                with self._staleness_lock:
                    self._staleness_refresh_in_flight = False

        threading.Thread(
            target=_refresh_in_background, daemon=True, name="quonfig-stale-refresh"
        ).start()
        return True

    def fallback_poller_active(self) -> bool:
        """``True`` when the Layer 2 HTTP fallback poller is currently
        scheduled. Mirrors sdk-node's ``fallbackPollerActive()`` — used by
        the chaos harness; the documented public ``connection_state()``
        accessor below is the customer-facing surface.

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
        """
        return self._fallback_poller is not None and self._fallback_poller.is_active()

    # ------------------------------------------------------------------
    # Failover + canonical-ordering observability (qfg-7h5d.1.8)
    #
    # Read by the failover/ordering chaos rigs and useful to customers who want
    # to introspect which leg served the held config and at what generation.
    # ------------------------------------------------------------------

    def ready(self) -> bool:
        """``True`` once the client has initialized AND installed at least one
        config envelope — i.e. it can actually serve resolved values.

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
        """
        return self._initialized.is_set() and self._store.install_count() > 0

    def resolved_from(self) -> str:
        """Which configured upstream leg produced the config the client is
        currently holding: ``"primary"`` (the first API URL), ``"secondary"``
        (any later URL reached via failover), or ``""`` before the first
        successful HTTP install. Reflects the HTTP config-fetch path only — SSE
        installs do not change it.

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
        """
        if self._resolved_from_index < 0:
            return ""
        if self._resolved_from_index == 0:
            return "primary"
        return "secondary"

    def held_generation(self) -> int:
        """``Meta.generation`` of the currently-installed envelope (0 before the
        first install or in datadir mode).

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
        """
        return self._store.get_generation()

    def config_install_count(self) -> int:
        """Number of envelopes installed over the client's lifetime (every
        install path). The reject-older guard keeps this from advancing on a
        same-or-older payload.

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
        """
        return self._store.install_count()

    def sse_failed_over_to_secondary(self) -> bool:
        """Whether the live SSE stream ever repointed to a non-primary leg.
        Always ``False`` by design — SSE is pinned to the primary stream and
        failover is an HTTP-only property — exposed so the chaos suite can
        assert that invariant (f05) and catch a regression that silently
        repoints the stream.

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
        """
        return self._sse_stream_index > 0

    # ------------------------------------------------------------------
    # Customer-visible health primitives (qfg-47c2.15)
    #
    # WARNING: do NOT wire either of these into a Kubernetes liveness
    # probe. They are diagnostic, not pass/fail. A liveness probe based on
    # SDK freshness amplifies transient network blips into restart
    # cascades. See README.
    #
    # Diagnostics never trigger the post-fork rebuild (cross-SDK ruling on
    # epic qfg-lv4n; sdk-ruby behaves the same). A health probe, a metrics
    # scrape or a log line must not start threads and fire a config fetch
    # from a forked child that has not evaluated anything yet. Until its
    # first content read such a child answers the pre-start state:
    # ``initializing`` / not ready / generation 0 / no liveness stamp.
    # ------------------------------------------------------------------

    def last_successful_refresh(self) -> Optional[datetime.datetime]:
        """Wall-clock time of the most recent successful refresh — the last
        moment the SDK confirmed its config source reachable and its held
        config current. This is a LIVENESS signal, not an install counter
        (qfg-41nh.11).

        Advanced by any envelope install (datadir load, initial HTTP fetch, SSE
        event, fallback poll — via the shared ``_fire_on_config_update`` hook)
        AND by an HTTP config fetch that completed successfully without
        installing — a 304 Not Modified, or a 200 the reject-older guard dropped
        as equal-or-older — and by a received-and-processed SSE message that was
        a guard no-op. Transport errors never advance it. Returns ``None``
        before the first successful refresh.

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
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

        With ``enable_sse=False`` there is no stream to be connected to and no
        SSE state will ever be observed, so the enum is derived from the
        liveness stamp alone — exactly like datadir mode: ``connected`` once a
        refresh has succeeded, ``initializing`` before that. An engaged poller
        is NOT reported as ``falling_back`` in that mode: ``falling_back``
        means "degraded — SSE died and Layer 2 caught the client", whereas
        poll-as-primary is the configured design and is not degraded
        (qfg-0xj3.1).

        Diagnostic-only: it never triggers the post-fork rebuild (qfg-lv4n).
        """
        if not self._enable_sse:
            with self._health_lock:
                last_refresh = self._last_successful_refresh
            return "connected" if last_refresh is not None else "initializing"
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

    def flush(self) -> None:
        """Synchronously drain and deliver all pending telemetry (qfg-0xj3.3).

        Evaluation summaries, context shapes, example contexts and failover
        counters are batched in memory and POSTed by a periodic background timer.
        On a serverless host (AWS Lambda, Vercel) that timer does not fire
        while the execution environment is frozen, so telemetry recorded during
        a request can sit in the collectors until the container is recycled —
        and is then lost. Call this before returning a response:

            value = client.get_string("my.flag")
            client.flush()
            return value

        A no-op when telemetry is disabled (no SDK key, or all collectors off).
        Never raises — a failing POST is logged, because a telemetry outage
        must not break the caller's request path. Mirrors sdk-node's
        ``flush()``. ``close()`` already flushes, so this is only needed when
        the process outlives the request.
        """
        self._ensure_rebuilt_after_fork()
        if self._telemetry is None:
            return
        try:
            self._telemetry.flush()
        except Exception as e:  # noqa: BLE001 — telemetry never breaks the caller
            logger.warning("Quonfig: telemetry flush failed: %s: %s", type(e).__name__, e)

    def close(self) -> None:
        # Deliberately does NOT call ``_ensure_rebuilt_after_fork``: closing a
        # client in a forked child that never used the SDK must return promptly
        # and must not stand a client up just to tear it down. Clearing the
        # flag also keeps a later read from resurrecting a closed client.
        #
        # It DOES take ``_fork_lock``, though, so it cannot interleave with a
        # first-use rebuild running on another thread: without it, a reporter
        # or poller the rebuild created a moment after the teardown walked past
        # it is started and never stopped — a live thread on a closed client
        # (qfg-lv4n.2). ``_rebuild_in_child`` re-checks ``_shutdown`` at the end
        # for the other ordering.
        with self._fork_lock:
            self._needs_rebuild_after_fork = False
            if not self._initialized.is_set():
                # Closed before it was ever initialized — in a forked child,
                # before its first use, that is the fresh ``Event`` the at-fork
                # handler installed, and clearing the rebuild flag above means
                # nothing will ever set it. Latch it now so later getters answer
                # immediately from what the client holds (defaults, for an empty
                # store) instead of blocking the whole ``init_timeout_ms`` and
                # then raising ``QuonfigInitTimeoutError``. A closed client
                # returning defaults instantly is the only sane semantic.
                self._finish_init()
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
