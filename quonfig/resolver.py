from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import mmh3

from .exceptions import QuonfigDecryptionError, QuonfigEnvVarNotSetError, QuonfigValueTypeError
from .types import Contexts, Value

if TYPE_CHECKING:
    from .store import ConfigStore

_MAX_UINT32 = 4_294_967_295.0  # math.MaxUint32

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

    def resolve(self, value: Value, contexts: Contexts, config_key: str = "") -> Any:
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
            return self._resolve_weighted(raw, value, contexts, config_key=config_key)

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
                raise QuonfigEnvVarNotSetError(f"Environment variable '{lookup}' is not set")
            return env_val
        raise QuonfigEnvVarNotSetError(f"Unknown provided source: {source!r}")

    def _resolve_weighted(
        self, raw: Any, value: Value, contexts: Contexts, config_key: str = ""
    ) -> Any:
        """Hash-based weighted value selection.

        Matches the Go/Node SDK algorithm:
        - Hash input: configKey + contextValue
        - Hash function: Murmur3 unsigned 32-bit / MaxUint32
        - Selection: running_sum >= threshold (inclusive)
        """
        if not isinstance(raw, dict):
            return None

        weighted_values = raw.get("weightedValues", [])
        hash_by = raw.get("hashByPropertyName", "")

        if not weighted_values:
            return None

        # Determine hash fraction
        from .context import get_context_value

        fraction: float
        if hash_by:
            hash_value, found = get_context_value(contexts, hash_by)
            if found and hash_value is not None:
                # Hash input: configKey + contextValue (matches Go/Node SDK)
                to_hash = f"{config_key}{hash_value}"
                uint32_val = mmh3.hash(to_hash, signed=False)
                fraction = uint32_val / _MAX_UINT32
            else:
                import random

                fraction = random.random()
        else:
            import random

            fraction = random.random()

        # Select variant using running-sum >= threshold (matches Go SDK)
        total_weight = sum(wv.get("weight", 0) for wv in weighted_values)
        if total_weight == 0:
            return None

        threshold = fraction * total_weight
        running_sum = 0
        selected = None
        for wv in weighted_values:
            running_sum += wv.get("weight", 0)
            if running_sum >= threshold:
                selected = wv.get("value")
                break

        # Fallback: return the first value (should not normally be reached)
        if selected is None:
            selected = weighted_values[0].get("value") if weighted_values else None

        if selected is None:
            return None

        # Recursively resolve the selected value
        if isinstance(selected, dict):
            inner_value = Value.from_dict(selected)
            return self.resolve(inner_value, contexts, config_key=config_key)
        return selected

    def _decrypt_value(self, raw: Any, decrypt_with_key: str) -> Any:
        """Look up encryption key from store and decrypt."""
        from .crypto import decrypt

        key_config = self.store.get(decrypt_with_key)
        if key_config is None:
            raise QuonfigDecryptionError(
                f"Encryption key config '{decrypt_with_key}' not found in store"
            )
        # Resolve the encryption key value — may be a plain string or provided (ENV_VAR)
        key_value = self._resolve_key_config_value(key_config)
        if key_value is None:
            raise QuonfigDecryptionError(
                f"Could not retrieve encryption key from '{decrypt_with_key}'"
            )
        if not isinstance(raw, str):
            raise QuonfigDecryptionError("Encrypted value must be a string")
        return decrypt(raw, key_value)

    def _resolve_key_config_value(self, config: Any) -> Any:
        """Extract the encryption key string from a config.

        Handles plain string and provided (ENV_VAR).
        """
        # Try environment rules first, then default rules
        rules = []
        for env in getattr(config, "environments", []):
            rules.extend(env.rules)
        if config.environment and config.environment not in getattr(config, "environments", []):
            rules.extend(config.environment.rules)
        rules.extend(config.default.rules)

        for rule in rules:
            if rule.value is not None:
                v = rule.value
                if v.type == "provided":
                    # Resolve the env var
                    try:
                        return self._resolve_provided(v.value)
                    except QuonfigEnvVarNotSetError:
                        return None
                elif isinstance(v.value, str):
                    return v.value
        return None

    def _coerce(self, raw: Any, vtype: str) -> Any:
        """Coerce raw value to the appropriate Python type."""
        if raw is None:
            # JSON's `null` is a legitimate native value — allow None to pass through.
            if vtype == "json":
                return None
            return None

        # JSON values must already be native on the wire — no stringified JSON.
        # This check runs OUTSIDE the try/except below so it propagates to callers.
        if vtype == "json":
            if isinstance(raw, str):
                raise QuonfigValueTypeError(
                    "json value must be a native JSON type "
                    "(dict/list/number/bool/None); stringified JSON is no longer allowed"
                )
            # dict / list / int / float / bool (and None handled above) all pass through.
            return raw

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
