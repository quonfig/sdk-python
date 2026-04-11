from __future__ import annotations

import json
import os
from typing import Any, TYPE_CHECKING

import mmh3

from .types import Contexts, Value
from .exceptions import QuonfigEnvVarNotSetError, QuonfigDecryptionError

if TYPE_CHECKING:
    from .store import ConfigStore

_MAX_32_FLOAT = 4_294_967_294.0

# Log level ordering for should_log()
LOG_LEVEL_ORDER = {
    "TRACE": 0,
    "DEBUG": 1,
    "INFO": 2,
    "WARN": 3,
    "WARNING": 3,
    "ERROR": 4,
    "FATAL": 5,
}


class Resolver:
    def __init__(self, store: "ConfigStore") -> None:
        self.store = store

    def resolve(self, value: Value, contexts: Contexts) -> Any:
        """Resolve a Value object to a Python native type."""
        if value is None:
            return None

        raw = value.value
        vtype = value.type

        # Handle provided (ENV_VAR) type
        if vtype == "provided":
            return self._resolve_provided(raw)

        # Handle weighted values
        if vtype == "weighted_values":
            return self._resolve_weighted(raw, value, contexts)

        # Handle decryption
        if value.confidential and value.decrypt_with:
            raw = self._decrypt_value(raw, value.decrypt_with)

        return self._coerce(raw, vtype)

    def _resolve_provided(self, raw: Any) -> Any:
        """Handle ENV_VAR provided values."""
        if isinstance(raw, dict):
            source = raw.get("source", "")
            lookup = raw.get("lookup", "")
        else:
            raise QuonfigEnvVarNotSetError(f"Invalid provided value format: {raw!r}")

        if source == "ENV_VAR":
            env_val = os.environ.get(lookup)
            if env_val is None:
                raise QuonfigEnvVarNotSetError(
                    f"Environment variable '{lookup}' is not set"
                )
            return env_val
        raise QuonfigEnvVarNotSetError(f"Unknown provided source: {source!r}")

    def _resolve_weighted(self, raw: Any, value: Value, contexts: Contexts) -> Any:
        """Hash-based weighted value selection."""
        if not isinstance(raw, dict):
            return None

        weighted_values = raw.get("weightedValues", [])
        hash_by = raw.get("hashByPropertyName", "")

        if not weighted_values:
            return None

        # Determine hash input
        from .context import get_context_value
        from .evaluator import Evaluator  # avoid circular at module level

        hash_value, found = get_context_value(contexts, hash_by) if hash_by else (None, False)

        # Compute percentage
        if hash_value is not None and found:
            # Use the config key + hash value as hash input
            # We need the config key; it's not directly available here so we use hash_by value
            to_hash = f"{hash_by}{hash_value}"
            int_value = mmh3.hash(to_hash, signed=False)
            percent = int_value / _MAX_32_FLOAT
        else:
            import random
            percent = random.random()

        # Select variant
        total_weight = sum(wv.get("weight", 0) for wv in weighted_values)
        if total_weight == 0:
            return None

        bucket = total_weight * percent
        bucket_sum = 0.0
        selected = None
        for wv in weighted_values:
            weight = wv.get("weight", 0)
            if bucket < bucket_sum + weight:
                selected = wv.get("value")
                break
            bucket_sum += weight

        if selected is None:
            selected = weighted_values[-1].get("value")

        if selected is None:
            return None

        # Recursively resolve the selected value
        if isinstance(selected, dict):
            inner_value = Value.from_dict(selected)
            return self.resolve(inner_value, contexts)
        return selected

    def _decrypt_value(self, raw: Any, decrypt_with_key: str) -> Any:
        """Look up encryption key from store and decrypt."""
        from .crypto import decrypt

        key_config = self.store.get(decrypt_with_key)
        if key_config is None:
            raise QuonfigDecryptionError(
                f"Encryption key config '{decrypt_with_key}' not found in store"
            )
        # The encryption key value is typically a plain string value
        # Evaluate to get the key string
        key_value = self._get_raw_string_value(key_config)
        if key_value is None:
            raise QuonfigDecryptionError(
                f"Could not retrieve encryption key from '{decrypt_with_key}'"
            )
        if not isinstance(raw, str):
            raise QuonfigDecryptionError("Encrypted value must be a string")
        return decrypt(raw, key_value)

    def _get_raw_string_value(self, config: Any) -> Any:
        """Extract the first matching rule's value as a string (for encryption keys)."""
        # Try environment rules first, then default rules
        rules = []
        if config.environment:
            rules.extend(config.environment.rules)
        rules.extend(config.default.rules)
        for rule in rules:
            if rule.value is not None:
                v = rule.value.value
                if isinstance(v, str):
                    return v
        return None

    def _coerce(self, raw: Any, vtype: str) -> Any:
        """Coerce raw value to the appropriate Python type."""
        if raw is None:
            return None

        try:
            if vtype == "bool":
                if isinstance(raw, bool):
                    return raw
                if isinstance(raw, str):
                    return raw.lower() in ("true", "1", "yes")
                return bool(raw)

            elif vtype == "int":
                if isinstance(raw, bool):
                    return int(raw)
                if isinstance(raw, int):
                    return raw
                return int(str(raw))

            elif vtype == "double":
                return float(raw)

            elif vtype == "string":
                return str(raw)

            elif vtype == "json":
                if isinstance(raw, (dict, list)):
                    return raw
                if isinstance(raw, str):
                    return json.loads(raw)
                return raw

            elif vtype == "string_list":
                if isinstance(raw, list):
                    return [str(x) for x in raw]
                return [str(raw)]

            elif vtype == "log_level":
                return str(raw).upper()

            elif vtype == "duration":
                import isodate
                if isinstance(raw, str):
                    duration = isodate.parse_duration(raw)
                    return duration.total_seconds()
                return float(raw)

            else:
                # Unknown type — return as-is
                return raw
        except Exception:
            return raw
