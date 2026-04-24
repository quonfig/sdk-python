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

## Configuration

| Param | Env var | Default |
|-------|---------|---------|
| `sdk_key` | `QUONFIG_SDK_KEY` | required for API mode |
| `api_urls` | `QUONFIG_API_URLS` (comma-separated; `QUONFIG_API_URL` deprecated fallback) | `https://api.quonfig.com` |
| `environment` | `QUONFIG_ENVIRONMENT` | `""` |
| `datadir` | `QUONFIG_DIR` | `None` |
| `init_timeout` | -- | `10.0` |
| `on_init_failure` | -- | `"raise"` |
| `on_no_default` | -- | `"error"` |
| `logger_key` | -- | `None` |
