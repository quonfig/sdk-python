# quonfig

Python SDK for Quonfig.

## Install

```bash
pip install quonfig
```

## Usage

```python
from quonfig import Quonfig

client = Quonfig(sdk_key="sdk-...")
client.init()

value = client.get_string("my.key", default="fallback")
enabled = client.is_feature_enabled("my.flag")
```

## Context

```python
# Per-call context
result = client.get_string("my.key", contexts={"user": {"plan": "pro"}})

# Bound context (for request handlers etc.)
user_client = client.with_context({"user": {"id": "u123", "plan": "pro"}})
enabled = user_client.is_feature_enabled("my.flag")

# Thread-local scoped context
with client.scoped_context({"user": {"id": "u123"}}):
    enabled = client.is_feature_enabled("my.flag")
```

## Dynamic log levels

```python
from quonfig import Quonfig

client = Quonfig(
    sdk_key="sdk-...",
    logger_key="log-level.my-app",  # config that drives per-logger rules
).init()

# Convenience form — SDK injects { "quonfig-sdk-logging": { "key": "my_app.auth" } }
# into context so a single config can route by logger path.
if client.should_log(logger_path="my_app.auth", desired_level="INFO"):
    print("auth event")

# Primitive form — for callers that want explicit control over the config key.
# No auto-prefixing: pass the full stored key.
if client.should_log(config_key="log-level.my-app", desired_level="DEBUG"):
    print("debug event")
```

`logger_path` is passed through verbatim — the SDK does not normalize it, so
callers can author config rules against whatever shape their host language
prefers (dotted, double-colon, slash, etc.).

### Dynamic log levels with stdlib `logging`

Attach `QuonfigLoggerFilter` to any logger or handler and the SDK will gate
records against `logger_key`. The record's `name` flows into context verbatim
as `quonfig-sdk-logging.key`, so a single config can drive per-logger rules.

```python
import logging
from quonfig import Quonfig, QuonfigLoggerFilter

client = Quonfig(sdk_key="sdk-...", logger_key="log-level.my-app").init()

root = logging.getLogger()
root.addFilter(QuonfigLoggerFilter(client))
```

### Dynamic log levels with `structlog`

`QuonfigLoggerProcessor` is a structlog processor. Place it after
`structlog.stdlib.add_log_level` so the level is populated on the event dict.

```python
import structlog
from quonfig import Quonfig, QuonfigLoggerProcessor

client = Quonfig(sdk_key="sdk-...", logger_key="log-level.my-app").init()

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        QuonfigLoggerProcessor(client),
        structlog.processors.JSONRenderer(),
    ],
)
```

`structlog` is an optional dependency — `QuonfigLoggerProcessor` raises
`ImportError` with an install hint if it isn't available. The stdlib filter
has no optional-dep concern.

## Datadir mode (local files)

```python
import os

client = Quonfig(datadir="/path/to/workspace", environment="production")
client.init()
```

## Datadir mode: auto-reload on file changes

When you initialize the SDK with `datadir="./path"`, configs are loaded once from
disk at `init()` time. Opt in to `data_dir_auto_reload` to have the SDK watch
the directory and re-read the envelope whenever files change — an editor save,
a `git pull`, or a build step.

```python
from quonfig import Quonfig

def on_update():
    print("Quonfig configs reloaded from disk")

client = Quonfig(
    datadir="./workspace-data",
    environment="development",
    data_dir_auto_reload=True,  # off by default — must be opted in
    on_config_update=on_update,
)
client.init()

# Edit a file under ./workspace-data and on_update fires within ~200ms.

# On shutdown, close() stops the watcher and clears any pending debounce timer.
client.close()
```

### When to enable

- Local development with the datadir checked out from git.
- Self-hosted servers that `git pull` the datadir on a schedule.
- CI jobs that mutate the datadir between assertions.

### When NOT to enable

- **Read-only / immutable filesystems** (some containers, AWS Lambda, scratch
  images). Watch registration may fail; the SDK degrades gracefully (logs the
  error and continues serving the envelope it loaded at `init()` time) but
  you're paying for nothing.
- **Build-time-embedded workflows** where the datadir is bundled into the
  artifact and never changes at runtime. Watching wastes a file descriptor and
  a watcher thread.
- **Production paths where reload timing matters** — e.g. you'd rather pin the
  envelope you shipped with and roll forward through a redeploy than have it
  shift under traffic.

Default is `False`; datadir mode is silent until you opt in.

### Behavior contract

- **Parse-then-swap.** If the new envelope fails to parse (truncated write,
  mid-`git pull` state, invalid JSON), the SDK logs the error and **keeps
  serving the previous envelope**. `on_config_update` is _not_ fired on parse
  failure — only on a successful swap.
- **Debounced.** Bursts of filesystem events (atomic-rename editor saves, `git
  pull` touching dozens of files) coalesce into a single re-read. Default
  window: **200ms** — long enough to absorb the 3–5 events typical editors emit
  in <50ms, short enough that interactive edits feel immediate. Tune via
  `data_dir_auto_reload_debounce_ms` if you need a different window.
- **Graceful degrade.** If watch registration fails (read-only fs, immutable
  container, missing path), the SDK logs and continues without watching — it
  does **not** raise from `init()`.
- **Symlinks.** The watcher resolves `datadir` to its real path at start time.
  Editing the file the symlink points at _is_ detected; atomic flips that
  retarget the link itself are **not**.
- **Shutdown.** `client.close()` signals the watcher's stop event and joins the
  daemon thread (≤2s). There is no separate handle to manage — the watcher
  lifecycle is tied to the client. The thread is a daemon, so a stuck join
  will not block process exit.

### Tuning the debounce window

```python
Quonfig(
    datadir="./workspace-data",
    data_dir_auto_reload=True,
    data_dir_auto_reload_debounce_ms=1000,  # wait a full second after the last event
)
```

The default (200ms) is tuned for interactive editing. Raise it if you have a
noisy producer (continuously regenerating files) and you'd rather see one
reload per second than per save. Lower it only if you've measured that 200ms
is meaningfully too slow for your use case.

See the [open-source / local how-to](https://docs.quonfig.com/docs/how-tos/open-source-local)
for the cross-SDK story (sdk-node, sdk-go, sdk-ruby, sdk-python, sdk-java).

## Serverless / AWS Lambda

On a host that freezes the process between invocations, the SSE stream and the
telemetry timers are dead weight — they can't run while the environment is
frozen. Build the client once at module scope, turn the background channels
off, and drive updates and telemetry from the handler instead.

```python
import os

from quonfig import Quonfig

# Module scope — once per execution environment, on cold start.
client = Quonfig(
    sdk_key=os.environ["QUONFIG_BACKEND_SDK_KEY"],
    enable_sse=False,                    # no long-lived stream
    fallback_poll_enabled=False,         # no background poller
    collect_evaluation_summaries=False,  # no background telemetry
    context_upload_mode="none",          # no background telemetry
).init()


def lambda_handler(event, context):
    client.update_if_staler_than(60_000)  # non-blocking; returns immediately
    body = client.get_string("greeting", default="hello")
    client.flush()                        # no-op here (telemetry off); delivers it if enabled
    return {"statusCode": 200, "body": body}
```

See the [Lambdas / Serverless docs](https://docs.quonfig.com/docs/sdks/python/lambdas)
for the full walkthrough.

### `enable_sse`

Defaults to `True` — the historical behavior, so existing callers are
unaffected. Combined with `fallback_poll_enabled` it selects the update
channel:

- `True` + `fallback_poll_enabled=True` (default): SSE is the primary channel;
  the HTTP poller engages only when SSE fails.
- `False` + `fallback_poll_enabled=True`: no SSE client is constructed and the
  HTTP poller becomes the primary channel, engaged immediately after init.
- `False` + `fallback_poll_enabled=False`: the client fetches once at `init()`
  and then moves only via `refresh()` / `update_if_staler_than()`.

The chosen channel is logged once at init. With `enable_sse=False` there is no
stream to be connected to, so `connection_state()` is derived from the liveness
stamp alone — `connected` once a refresh has succeeded, and never
`falling_back` (poll-as-primary is the configured design, not a degraded
state).

### `update_if_staler_than(max_age_ms)`

Stale-while-revalidate, and **non-blocking** — it never puts a network
round-trip on the request path.

- Fresher than `max_age_ms`: returns `False`, having done nothing.
- Stale (or never refreshed): fires one refresh on a background daemon thread
  and returns `True` immediately. The caller keeps serving the config already
  in memory; a later call sees the fresher one.
- Already refreshing: returns `False`. Refreshes are coalesced — at most one is
  ever in flight, so a per-request caller can't stack threads against a slow or
  unreachable upstream.

`True` means "a refresh was triggered", not "a refresh completed". A worker
frozen mid-fetch completes on the next thaw; installing then is safe, because
the reject-older guard drops a payload that isn't newer than what the client
holds.

### `flush()`

Synchronously drains and POSTs all pending telemetry. The periodic timer that
normally delivers it doesn't fire while the environment is frozen, so anything
recorded during a request would sit in the collectors until the container is
recycled — and be lost. A no-op when telemetry is disabled, and it never raises:
a failing POST is logged. `close()` already flushes, so this is only needed when
the process outlives the request.

## Forking servers (Gunicorn `--preload`, Celery, uWSGI, `multiprocessing`)

**No wiring required on POSIX.** Importing `quonfig` installs a child-only
`os.register_at_fork` handler. After a fork, the child re-initializes on its
first use of the client, exactly like a newly constructed client: it fetches
its own config and starts its own threads. It does not evaluate from the
parent's snapshot.

```python
# app.py, imported by `gunicorn --preload -w 8 app:app`
from quonfig import Quonfig

client = Quonfig(sdk_key="sdk-...").init()   # built once, in the master
```

Each forked worker gets its own transport, its own SSE stream or HTTP poll
loop, its own telemetry buffers, and fresh locks.

This covers every fork-based deployment:

| Host | Fork point |
|------|------------|
| Gunicorn `--preload` (sync, gthread; see gevent below) | master forks each worker |
| Celery, default `prefork` pool | worker forks each child process |
| uWSGI (without `--lazy-apps`) | master forks each worker |
| `multiprocessing` / `ProcessPoolExecutor` with the `fork` start method | pool forks each process |
| a bare `os.fork()` in application code | wherever you call it |
| `subprocess` with `preexec_fn=` | CPython runs at-fork handlers in the pre-exec child |

### The rebuild is lazy

The at-fork handler itself does no network I/O, starts no threads and takes no
inherited lock. It only drops what the child inherited and marks the client for
rebuild; the re-initialization runs on the child's first SDK call. Two
consequences worth knowing:

- A forked child that never uses the SDK costs nothing — no fetch, no stream,
  no threads. That matters because at-fork handlers run on **every** fork in
  the process, including `multiprocessing` workers doing unrelated work and the
  pre-exec child of any `subprocess` spawn that passes `preexec_fn`.
- The **first evaluation** in a forked child pays one config fetch and blocks
  for it, under the usual `init_timeout_ms` / `on_init_failure` rules. What
  triggers the rebuild is a content read: `get_*`, `is_feature_enabled`, the
  `*_details` getters, `should_log`, `with_context` and the values you read
  through it, `keys()`, `raw_config()`, `refresh()`, `update_if_staler_than()`
  and `flush()`. If you would rather pay that before your first request, call
  `init()` (or any getter) in the worker's post-fork hook.
- **Diagnostics never trigger the rebuild.** `connection_state()`, `ready()`,
  `held_generation()`, `last_successful_refresh()` and the other health
  accessors answer the child's pre-start state — `initializing`, `False`, `0`,
  `None` — without starting a thread or making a request, until that child's
  first content read. A liveness probe, a metrics scrape or a log line in a
  forked worker therefore costs nothing and cannot stand a client up behind
  your back.

Details, if you need them:

- **The parent is never touched.** Nothing is torn down before the syscall and
  nothing changes in the parent afterwards, so a process that forks and then
  keeps evaluating (a Celery worker running a `fork` inside a task, a master
  that also serves) is unaffected.
- **The child never reuses inherited state.** Threads that did not survive the
  fork are dropped rather than joined, inherited sockets are dropped rather
  than closed (they still belong to the parent), and every lock is replaced —
  a lock held by a non-forking thread at fork time can never be released in the
  child.
- **`connection_state()` tells the truth in a child.** It reports
  `initializing` — before the rebuild and after it, until the child's own
  first refresh actually succeeds — rather than inheriting the parent's
  `connected`.
- **Telemetry is not duplicated.** The child starts with empty buffers; the
  parent delivers the window it recorded.
- On its first use the child logs one line naming its pid and the components it
  rebuilt.
- A client you **closed** before forking stays closed in the child. Calling
  `close()` in a child before its first use is instant, and getters after it
  return their defaults immediately instead of blocking `init_timeout_ms`.
- A client you **constructed but never `init()`ed** is not started behind your
  back — but you can start it yourself. Construct it in the master and call
  `init()` in the child (Gunicorn's `post_fork`, Celery's `worker_process_init`)
  and you get a full client: its own transport, its own update channel, its own
  telemetry.
- **Re-forking before first use is safe.** A child that forks again before it
  has touched the SDK hands the pending rebuild down; the grandchild rebuilds
  on its own first use exactly as its parent would have.

### Start methods

The `spawn` start method (the default for `multiprocessing` on macOS and
Windows) is unaffected: those children run a fresh interpreter and build their
own client from scratch, so there is nothing inherited to rebuild. On platforms
without `os.fork` the handler is never registered.

`forkserver` children **are** forks — of the forkserver process, not of your
main process — so the handler does run in them. It is a no-op unless a module
you preloaded via `multiprocessing.set_forkserver_preload` built a client;
normally the forkserver process holds none, so there is nothing to reset. Worth
knowing because **Python 3.14 makes `forkserver` the default start method on
Linux.**

CPython 3.12+ emits a `DeprecationWarning` when `os.fork()` is called in a
multi-threaded process, and the SDK's own background threads (SSE, fallback
poll, telemetry) are enough to trigger it. The warning is about fork-plus-
threads in general, not about this SDK — the child-only handler is what makes
the fork survivable, not what causes the warning.

### gevent

**Build the client after the fork under gevent, or do not use `--preload`.**
Greenlets are not threads, and `os.register_at_fork` cannot help with them: if
the parent is monkey-patched, its SSE greenlet is still scheduled in the
child's copy of the hub and still reading the socket the parent is reading, so
the **parent** silently loses events (observed in 3 of 5 runs).

Gunicorn's own `--preload -k gevent` shape is fine as long as the *master* is
not monkey-patched — modern Gunicorn patches inside
`GeventWorker.init_process()`, which runs in the worker after the fork. If your
application calls `monkey.patch_all()` at import time under `--preload`, the
master is patched and you are in the broken shape. Build the client in
Gunicorn's `post_fork` hook instead.

### uWSGI

Pass `--enable-threads`. uWSGI disables the Python threading machinery by
default, and the SDK's SSE stream, fallback poller and telemetry reporter are
all background threads — without that flag none of them run. (`--lazy-apps`
additionally sidesteps the fork by loading the app in each worker.)

### macOS

`requests` resolves proxy settings through `urllib.request.proxy_bypass` ->
`_scproxy` -> SystemConfiguration, which enters the Objective-C runtime.
libobjc deliberately kills a process that does that in a child forked from a
multi-threaded parent, so **any** HTTP with `requests` in a forked child can
crash on macOS, with or without this SDK — a plain `requests.get()` in a forked
child reproduces it with Quonfig nowhere in the process.

Because the rebuild is lazy, a forked child that never calls the SDK never goes
near it. If your child *does* call the SDK on macOS, either:

- set `NO_PROXY` to cover the API host (e.g. `NO_PROXY=quonfig.com`, or
  whatever `QUONFIG_DOMAIN` points at) — `requests` then short-circuits before
  proxy detection. The underlying `requests` knob is `Session.trust_env =
  False`, which the SDK does not expose; `NO_PROXY` is the supported lever; or
- build the client after the fork.

Datadir mode has a second macOS-only hazard: with `data_dir_auto_reload=True`
the child aborts (`SIGABRT`) when the rebuild starts its filesystem watcher,
because `watchfiles` reaches FSEvents through the same Objective-C runtime —
build the client after the fork if you need auto-reload on macOS.

**Linux is unaffected** — these are macOS system-library issues, and macOS is a
development platform for this SDK, not a deployment one.

## Configuration

| Param | Env var | Default |
|-------|---------|---------|
| `sdk_key` | `QUONFIG_BACKEND_SDK_KEY` | required for API mode |
| `api_urls` | -- (derived from `QUONFIG_DOMAIN`) | `["https://primary.quonfig.com", "https://secondary.quonfig.com"]` |
| `telemetry_url` | -- (derived from `QUONFIG_DOMAIN`) | `https://telemetry.quonfig.com` |
| `environment` | `QUONFIG_ENVIRONMENT` | `""` |
| `datadir` | `QUONFIG_DIR` | `None` |
| `init_timeout_ms` | -- | `10_000` |
| `on_init_failure` | -- | `"raise"` |
| `on_no_default` | -- | `"error"` |
| `logger_key` | -- | `None` |
| `enable_sse` | -- | `True` |
| `fallback_poll_enabled` | -- | `True` |
| `data_dir_auto_reload` | -- | `False` |
| `data_dir_auto_reload_debounce_ms` | -- | `200` |

### `QUONFIG_DOMAIN`

A single env var governs the api, sse, and telemetry URL defaults:

| Env var | Default | Effect |
|---------|---------|--------|
| `QUONFIG_DOMAIN` | `quonfig.com` | Sets `api_urls` to `https://primary.${DOMAIN}` + `https://secondary.${DOMAIN}` and `telemetry_url` to `https://telemetry.${DOMAIN}`. SSE host is derived by prepending `stream.` to the api host. |

Resolution order (highest wins):

1. Explicit `api_urls=` / `telemetry_url=` kwargs (local-dev escape hatch).
2. `QUONFIG_DOMAIN` env var.
3. Hardcoded default `quonfig.com`.

The previously-supported `QUONFIG_API_URL`, `QUONFIG_API_URLS`, and
`QUONFIG_TELEMETRY_URL` env vars have been removed.

## Failover & `QUONFIG_DOMAIN`

By default the SDK derives every hostname from `QUONFIG_DOMAIN` (default
`quonfig.com`):

| Role                     | URL                                    |
|--------------------------|----------------------------------------|
| Config fetch (primary)   | `https://primary.quonfig.com`          |
| SSE stream (primary)     | `https://stream.primary.quonfig.com`   |
| Config fetch (secondary) | `https://secondary.quonfig.com`        |
| SSE stream (secondary)   | `https://stream.secondary.quonfig.com` |
| Telemetry                | `https://telemetry.quonfig.com`        |

Set `QUONFIG_DOMAIN` to move all of them together (e.g.
`QUONFIG_DOMAIN=quonfig-staging.com`). **Automatic failover and hedging between
the primary and the secondary are on by default** — the secondary runs on
separate infrastructure, and the SDK fails over to it if the primary is
unreachable and hedges to it if the primary is slow.

An explicit `api_urls=` replaces the derived list wholesale. To keep automatic
failover with custom URLs, **pass both a primary and a secondary URL**:

```python
client = Quonfig(
    sdk_key="your-sdk-key",
    api_urls=[
        "https://primary.your-proxy.example",
        "https://secondary.your-proxy.example",
    ],
)
```

A single URL disables failover, and the SDK logs a warning at init. See
https://docs.quonfig.com/docs/explanations/architecture/resiliency for the full
model.

## Health primitives

The client exposes two diagnostic getters:

```python
client.last_successful_refresh()  # -> datetime | None
client.connection_state()         # -> "connected" | "disconnected" | "falling_back" | "initializing"
```

- `last_successful_refresh()` is the wall-clock time of the most recent
  installed config envelope. Updated on every install path (datadir load,
  initial HTTP fetch, SSE event, fallback poll). `None` before the first
  install.
- `connection_state()` reports the SDK's current view of its delivery
  pipeline. `falling_back` means SSE is down and the HTTP fallback poller
  is engaged. (With `enable_sse=False` there is no stream to fall back
  from, so an engaged poller reports `connected` — see the
  [`enable_sse`](#enable_sse) section.)

> Do not wire `last_successful_refresh()` or `connection_state()` directly into a Kubernetes liveness probe. These signals are diagnostic, not pass/fail. A liveness probe based on SDK freshness will amplify transient network blips into restart cascades.

Compose your own threshold (e.g. "alert if stale > 10 minutes AND state
is `disconnected`") rather than treating either primitive as binary
health.
