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

## Configuration

| Param | Env var | Default |
|-------|---------|---------|
| `sdk_key` | `QUONFIG_SDK_KEY` | required for API mode |
| `api_urls` | -- (derived from `QUONFIG_DOMAIN`) | `["https://primary.quonfig.com", "https://secondary.quonfig.com"]` |
| `telemetry_url` | -- (derived from `QUONFIG_DOMAIN`) | `https://telemetry.quonfig.com` |
| `environment` | `QUONFIG_ENVIRONMENT` | `""` |
| `datadir` | `QUONFIG_DIR` | `None` |
| `init_timeout_ms` | -- | `10_000` |
| `on_init_failure` | -- | `"raise"` |
| `on_no_default` | -- | `"error"` |
| `logger_key` | -- | `None` |
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
  is engaged.

> Do not wire `last_successful_refresh()` or `connection_state()` directly into a Kubernetes liveness probe. These signals are diagnostic, not pass/fail. A liveness probe based on SDK freshness will amplify transient network blips into restart cascades.

Compose your own threshold (e.g. "alert if stale > 10 minutes AND state
is `disconnected`") rather than treating either primitive as binary
health.
