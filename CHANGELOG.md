# Changelog

## Unreleased

- **Fix: `last_successful_refresh()` now tracks liveness, not just installs
  (qfg-41nh.11).** The stamp is a liveness signal — the last moment the SDK
  confirmed its config source reachable and its held config current — but it
  previously advanced only on an envelope install. A healthy long-lived client
  parked on 304s (or same-generation payloads) under-reported liveness: the
  stamp froze even though every fetch succeeded. It now also advances on an HTTP
  config fetch that completed successfully WITHOUT installing (a 304 Not
  Modified, or a 200 the reject-older guard dropped as equal-or-older) and on a
  received-and-processed SSE message that was a guard no-op. Transport errors
  still never advance it. Diagnostic-only accessor; no behavior change to config
  resolution and no new dependencies. Mirrors sdk-go.
- **Feat: warn when an explicit `api_urls` disables failover (qfg-41nh.26).**
  The default (and every `QUONFIG_DOMAIN`-derived) `api_urls` list carries a
  primary and a secondary leg, and the SDK hedges/fails over between them. An
  explicit `api_urls=` with a single entry silently dropped the secondary; the
  SDK now logs a one-line `WARNING` at init pointing the caller at the fix (pass
  both a primary and a secondary URL). Behavior is otherwise unchanged; no new
  dependencies. A new README "Failover & `QUONFIG_DOMAIN`" section documents the
  URL derivation and the failover model. Mirrors sdk-go.
- **Feat: the SDK now emits failover telemetry (qfg-41nh.18).** A new
  `failover` telemetry event carries the operational counters for the
  secondary-delivery hardening: `hedgeFired` (config-fetch cycles where the
  parallel hedge fired its secondary leg), `guardRejected` (installs dropped by
  the reject-older ordering guard, on both the HTTP config-fetch path and the
  SSE message path), and `resolvedFromPrimary` / `resolvedFromSecondary` (which
  upstream leg served each successful HTTP install; SSE/datadir installs are not
  counted). The event carries no user data, rides any enabled telemetry stream
  (so a full telemetry opt-out still suppresses it), and is emitted only when at
  least one counter is non-zero in the flush window — a healthy steady-state
  client emits nothing. Wire keys are camelCase, matching sdk-go and the
  api-telemetry schema. Mirrors sdk-go's `FailoverAggregator`.

## 1.1.1 - 2026-07-03

- **Fix: per-leg config-fetch aborts are now true wall-clock deadlines
  (qfg-41nh.10).** Previously the per-leg "hard abort" was a scalar `requests`
  timeout, which only bounds connect + between-bytes gaps — a slow-drip
  upstream (one byte before every timeout tick, body never finishing) reset
  the read timer forever, wedging the hedge's result drain and with it the
  fallback-poll loop, exactly when SSE was already down (both refresh layers
  dead, zero signal). Every leg — hedged and sequential — now drains the
  response body under an explicit monotonic deadline sized from its per-URL
  budget, and the hedge drain additionally bounds its queue reads as a
  backstop, abandoning (with a warning) any leg that outlives its deadline.
  A leg's ETag is now recorded only after its body fully arrives and decodes,
  so an aborted read can no longer make the next conditional request 304
  against a payload that was never installed.
- **Fix: the fallback poller now fetches immediately on engage
  (qfg-41nh.10).** The poller only engages when SSE is already unavailable, so
  the held config is already suspect — waiting a full interval before the
  first fetch put first-data-after-SSE-loss at grace + interval (~180s with
  defaults) vs ~120s in sdk-go, which fetches on engage. The first fetch now
  fires immediately; the interval cadence follows. An engage right after init
  is harmless (per-leg ETags turn the redundant fetch into a 304).
- **Fix: `resolved_from()` is now stamped under the store lock, atomically
  with the accepted install (qfg-41nh.10).** The docstring already promised
  this; the stamp actually happened after the lock was released, so a reader
  could observe a new generation paired with a stale resolved-from index.
  `ConfigStore.update` gained an optional `on_installed` callback that runs
  under the lock only on accepted installs (additive, backward-compatible).

## 1.1.0 - 2026-07-01

- **Feat: the HTTP config-fetch is now a parallel-failover hedge (qfg-7h5d.1.14).**
  On init/refresh, the SDK fires the **primary** URL first. If it answers within
  the hedge delay (~2s) it wins and the **secondary is never contacted** (cold
  standby — a healthy system adds zero secondary load). Only if the primary is
  slow past the hedge delay **or** errors fast does the SDK *also* fire the
  secondary **in parallel** — it does not cancel the primary and does not wait
  for one-or-the-other. Each arriving leg is installed through the existing
  reject-older guard, so watermark-max falls out: a higher generation wins, a
  late older payload never regresses an established client, and a late newer
  payload heals forward. `ready()` latches on the first successful install;
  heal-forward happens after. Each leg uses its own ETag slot.
- **Feat: two additive options — `hedge_delay_ms` (default `2000`) and
  `config_fetch_hedge_abort_ms` (default `6000`).** `hedge_delay_ms` is how long
  to wait for the primary before also firing the secondary in parallel.
  `config_fetch_hedge_abort_ms` is the per-leg hard-abort deadline on the hedged
  path; it must exceed the longest healable primary latency (so a late-but-newer
  primary heals forward instead of aborting) and should be `< init_timeout_ms`
  (so the init-path heal leg is not clipped) — the client logs a warning at
  construction when `init_timeout_ms <= config_fetch_hedge_abort_ms`. The
  existing `config_fetch_timeout_ms` is unchanged (it still governs the
  sequential `fetch` path).
- **Behavioral notes (backward-compatible, minor bump).** These follow from the
  hedge and are intentional:
  - `resolved_from()` may now return `"primary"` in a fast-both topology where
    1.0.0 returned `"secondary"` (1.0.0 reached the secondary on the sequential
    failover path; the hedge keeps a fast primary on `"primary"`).
  - An extra post-`ready()` `on_config_update` callback may fire on heal-forward
    (a late-but-newer leg installs after readiness has already latched).
  - ETags are now tracked **per leg** (per base URL) rather than as a single
    shared value, so a 304 from one leg can no longer mask the other.
- **Install-guard carve-out for unversioned snapshots.** A delivery payload
  whose `generation` is absent or `<= 0` (e.g. from a server that predates the
  generation watermark) is installed by an established client rather than
  rejected as older. Defensive back-compat guard — with servers that emit true
  generations it never triggers.

## 1.0.0 - 2026-06-06

- **Stable 1.0.0 release.** The Quonfig Python SDK is now declared stable. No API or
  behavior changes from 0.0.21 — this is a coordinated 1.0.0 version stamp across
  the entire Quonfig SDK family.

## 0.0.21 - 2026-06-02

- **Dev-context injection is now default-on (qfg-bw7g.4).** `enable_quonfig_user_context` is now `Optional[bool]` (`None` = unset). When left unset it defaults to **on**, gated solely by the presence of the qfg-login tokens file; the loader no-ops without it, so this stays inert in production. Precedence: explicit `enable_quonfig_user_context` ?? `QUONFIG_DEV_CONTEXT` env (`true`/`false`) ?? `True`. Pass `enable_quonfig_user_context=False` or set `QUONFIG_DEV_CONTEXT=false` to opt out.

## 0.0.20 - 2026-05-30

- **Fix: auto-load the SDK key from `QUONFIG_BACKEND_SDK_KEY`, not `QUONFIG_SDK_KEY` (qfg-ujcq).** A bare `Quonfig()` (no `sdk_key=`) now reads the SDK key from `QUONFIG_BACKEND_SDK_KEY` — the same env var every other Quonfig SDK (go/node/ruby/java) and the `qfg run` CLI auto-load, and the var the Python docs already documented. Previously the SDK read `QUONFIG_SDK_KEY`, a python-only outlier, so `export QUONFIG_BACKEND_SDK_KEY=...` followed by `Quonfig()` silently resolved no key. Clean switch with no fallback (alpha-phase, no backward compatibility); the legacy var was never documented.

## 0.0.19 - 2026-05-29

- **Fix: per-environment overrides now apply in HTTP+SSE delivery mode; `meta.environment` is authoritative (qfg-xpln.3).** The evaluator now derives its target environment from the `meta.environment` field on the delivered config envelope rather than from any client-side setting, so per-environment rule overrides resolve correctly when the SDK runs in delivery (HTTP+SSE) mode. Previously a config's environment-specific rules could be skipped because the evaluator lacked the authoritative environment that api-delivery had already resolved.
- **Fix: explicit environment pin is datadir-only and is ignored (with a WARN) in delivery mode (qfg-pinh).** An explicit environment pin — `environment=` constructor kwarg or the `QUONFIG_ENVIRONMENT` env var — now applies only in datadir mode. In delivery mode the pin is ignored because `meta.environment` from the delivered envelope is authoritative; the SDK logs a `WARNING` when a pin is supplied in delivery mode so the misconfiguration is visible rather than silently honored.

## 0.0.18 - 2026-05-28

- **Feat: collapse `init_timeout` + `initialization_timeout_sec` into canonical `init_timeout_ms` (qfg-o8zr).** sdk-1.0 unification, Section 1. The SDK previously accepted two seconds-based init-timeout kwargs (`init_timeout: float` and `initialization_timeout_sec: float`, the latter winning); only sdk-ruby (also renamed in qfg-39za) and sdk-python were on seconds while sdk-node uses `initTimeout` (ms) and sdk-javascript uses `timeout` (ms). New canonical kwarg `init_timeout_ms: int = 10_000` (milliseconds) aligns all SDKs. The legacy `init_timeout` and `initialization_timeout_sec` kwargs are kept as deprecated aliases for one minor cycle — each emits a `DeprecationWarning` and is forwarded as `value * 1000` into `_init_timeout_ms`. The canonical kwarg wins when more than one is passed.

## 0.0.17 - 2026-05-21

- **Fix: datadir loader coerces `int`/`double` values to numbers at load time (qfg-38sf.8).** Quonfig config files store `int` and `double` value fields as JSON strings on disk (`{"type": "int", "value": "123"}`). The datadir loader previously passed these through verbatim, leaving the loaded envelope's `Value.value` as a string until downstream unwrap coercion ran. `load_datadir` now runs a recursive `coerce_numeric_values` walk over each parsed config dict before building the `ConfigResponse`, so the loaded envelope always carries real numbers — matching api-delivery and sdk-go. The walk covers default/environment rules, criteria `valueToMatch`, weighted-value arms, and variants uniformly; an unparseable numeric string is left as-is (passthrough, never raises).
- **Feat: new public `raw_config(key)` accessor on `Quonfig` (qfg-bwwj).** Returns the raw loaded `ConfigResponse` envelope for a key (pre-evaluation, pre-unwrap), or `None` if no such config is loaded. Mirrors sdk-node's `rawConfig` — intended for advanced usage and tooling that needs the on-the-wire config shape rather than a resolved value.
- **Chore: pin `integration-test-data` to `v2026.05.20` in CI and guard against stale generated tests (#22).**
- **Chore: dependency bumps.** Runtime: `requests` 2.34.1 → 2.34.2 (#19). Dev tooling: `pytest` 8.4.2 → 9.0.3 (#17, major), `mypy` 1.20.2 → 2.1.0 (#20, major). CI actions: `actions/upload-artifact` 4.6.2 → 7.0.1 (#13), `jdx/mise-action` 2.3.1 → 4.0.1 (#14), `pypa/gh-action-pypi-publish` 1.9.0 → 1.14.0 (#15), `actions/setup-go` 5.6.0 → 6.4.0 (#16).

## 0.0.16 - 2026-05-19

- **Feat: opt-in `data_dir_auto_reload` for datadir mode (qfg-mol-3gy).** Two new constructor kwargs — `data_dir_auto_reload` (default `False`) and `data_dir_auto_reload_debounce_ms` (default `200`). When enabled in datadir mode, the SDK watches the resolved datadir via [`watchfiles`](https://github.com/samuelcolvin/watchfiles) (Rust-backed `notify` wrapper) and re-runs `load_datadir` on each debounced burst, parse-then-swapping the envelope through the existing `_store.update` path and firing the existing `on_config_update` callback. Mirrors sdk-node's `dataDirAutoReload`. Behaviour contract: parse-then-swap (a mid-write JSON garble logs and is dropped — the previous envelope keeps serving and `on_config_update` does **not** fire on parse failure); graceful degrade on read-only / immutable filesystems (registration failures log a warning and continue without watching — `init()` does not raise); symlinked datadirs resolve to the real path at start time; `client.close()` signals the watcher's stop event and joins the daemon thread (≤2s). Adds `watchfiles>=0.21,<2.0` as a runtime dep.

## 0.0.15 - 2026-05-14

- **Fix: no more silent SSE reconnect on a clean `sseclient` EOF (qfg-47c2.31).** When the SSE stream closed cleanly (server-side EOF rather than a network error), the client treated it as a normal end-of-iteration and silently stopped receiving updates without re-establishing the stream or engaging the fallback poller. The stream now distinguishes a clean EOF from an intentional shutdown and reconnects, so a server-initiated stream close no longer leaves the SDK serving stale config indefinitely.
- **Feat: dev-context injection from `~/.quonfig/tokens.json` (qfg-jopa).** In local development the SDK now reads a developer context from `~/.quonfig/tokens.json` if present and merges it into evaluation context, so engineers can target themselves without wiring context through application code. No effect when the file is absent (i.e. in CI and production).
- **Breaking (build/runtime): minimum Python is now 3.10; `urllib3` bumped to 2.7.0 and `requests` to 2.34.1 (qfg-or1x).** Python 3.9 is dropped from the support matrix. This is a packaging-level change only — no SDK public API changed — but installs on Python 3.9 will no longer resolve. The `urllib3`/`requests` bumps clear CVE-2026-44431 / CVE-2026-44432, which were previously suppressed because the fixed `urllib3` requires Python ≥ 3.10.
- **Chore: cross-SDK chaos harness wired as a release gate.** The chaos harness (`scripts/run-chaos.sh`) now runs in CI: a smoke subset on every push and PR, and the full suite as a publish gate in `release.yaml`. It is skipped on Dependabot PRs, which run with a restricted token context that cannot check out the private `api-delivery` repo the harness boots against.
- **Chore: dependency bumps.** Runtime: `mmh3` 4.x → 5.2.1, `isodate` 0.6.1 → 0.7.2. Dev tooling: `ruff` 0.4 → 0.15 (formatter style rules updated; source reformatted accordingly), `pytest-cov` 5 → 7, `responses` 0.25 → 0.26. CI actions: `actions/checkout`, `actions/setup-python`, and `pypa/gh-action-pypi-publish` bumped to current major versions.

## 0.0.14 - 2026-05-11

- **Feat: `last_successful_refresh()` and `connection_state()` health primitives (qfg-47c2.15).** Two new diagnostic getters on `Quonfig`: `last_successful_refresh()` returns the wall-clock time of the most recent installed envelope (or `None` before the first install); `connection_state()` returns one of `connected` / `disconnected` / `falling_back` / `initializing`. Stamped on every install path (datadir, initial fetch, SSE event, fallback poll). No `healthy()` boolean is exposed — per the SDK hardening plan, customers will wire a binary into k8s liveness probes and amplify transient blips into restart cascades; the README documents this explicitly.
- **Feat: Layer 2 fallback HTTP polling, SSE-failure-only (qfg-47c2.8).** Replaces the previous always-on parallel poll (which doubled bandwidth and had no reconcile logic) with an on-failure-only fallback. New options: `fallback_poll_enabled` (default `True`) and `fallback_poll_interval_ms` (default 60000). The fallback poller engages when SSE fails to make its initial connection OR when SSE has been disconnected for at least 2× the poll interval (default 120s) after a successful connect; it disengages immediately on SSE recovery. Behaviour now matches sdk-node, sdk-go, and sdk-ruby — verified by the cross-SDK chaos harness (scenarios 01–06, 09, 10 pass; 07/08 are toxiproxy harness limitations; 11 is a 30-min steady-state run not invoked by default). **Behaviour change (alpha, no semver hold):** in-process bandwidth drops to one HTTP fetch per minute *only when SSE is unavailable*, rather than one per minute on top of SSE — applications that depended on the parallel reconcile path should rely on SSE for live updates.
- **Feat: `on_config_update` and `on_sse_connection_state_change` callbacks.** Two new constructor options matching sdk-go (`WithOnConfigUpdate` / `WithSSEStateCallback`) and sdk-node (`onConfigUpdate` / `onSSEConnectionStateChange`). `on_config_update` fires after each successful config install (initial fetch, datadir load, SSE event, fallback poll). `on_sse_connection_state_change` fires on every SSE state edge (`connecting` / `connected` / `error` / `disconnected`). Caller exceptions are caught by the SDK supervisor and logged at `error` level (chaos scenario 10 contract).
- **Chore: chaos harness wired in.** New `chaos/test_chaos.py` runner + `scripts/run-chaos.sh` launcher mirror sdk-node's. Excluded from default `pytest` collection via `testpaths = ["tests"]` in `pyproject.toml`; run with `./scripts/run-chaos.sh` (boots toxiproxy + api-delivery first).

## 0.0.13 - 2026-05-10

- **Feat: expose `variant` and `flag_metadata` on `EvaluationDetails` (qfg-9dbl).** OpenFeature-style evaluation details now surface the resolved variant name and any flag-level metadata, matching the sdk-node and sdk-ruby surfaces so OpenFeature provider code can read them without re-resolving the flag.
- **Feat: `IS_PRESENT` and `IS_NOT_PRESENT` targeting operators (qfg-7jnb.7).** Both take only `propertyName` (no `valueToMatch`). `IS_PRESENT` resolves the dotted path against the merged context and returns `True` iff the path resolves AND the value is not `None`. Type-agnostic — empty string `""`, `0`, and `False` all count as **present**; only `None` and missing keys (including missing nested paths) are absent. `IS_NOT_PRESENT` is the negation. Implemented in `Evaluator._criterion_matches` so the operator can see the dotted-path `found` flag, with `is not None` checks deliberately used instead of truthy-falsy `if not value:` (which would mis-classify `""`, `0`, and `False` as absent). Matches sdk-node, sdk-go, sdk-ruby wire behaviour. Closes the integration-test parity gap that left 7 pytest cases red since the operators landed in `integration-test-data`.
- **Chore: add ruff format CI gate.** CI now runs `ruff format --check` on every push so style drift can't sneak into a release. Part of the cross-repo formatter rollout (qfg-i5x5).
