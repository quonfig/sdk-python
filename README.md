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
| `api_urls` | `QUONFIG_API_URL` | `https://api.quonfig.com` |
| `environment` | `QUONFIG_ENVIRONMENT` | `""` |
| `datadir` | `QUONFIG_DIR` | `None` |
| `init_timeout` | -- | `10.0` |
| `on_init_failure` | -- | `"raise"` |
| `on_no_default` | -- | `"error"` |
