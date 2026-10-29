from __future__ import annotations

import copy
import hashlib
import os
import re
from typing import TYPE_CHECKING, Any, Optional, Tuple

import mmh3

from .duration import parse_duration_millis
from .exceptions import (
    QuonfigDecryptionError,
    QuonfigEnvVarCoerceError,
    QuonfigEnvVarNotSetError,
    QuonfigValueTypeError,
)
from .types import Contexts, Value

# Mirrors Reforge SDK config_value_unwrapper.py:24 — kept identical so
# the redacted format is byte-for-byte compatible across SDKs.
CONFIDENTIAL_PREFIX = "*****"


def compute_reportable_value(value: Value) -> Optional[str]:
    """Return the redacted telemetry form for a confidential / encrypted value.

    Matches Reforge SDK ``reportable_wrapped_value`` (sdk-python
    ``config_value_unwrapper.py:69-78``):

    * Trigger: ``value.confidential`` is True OR ``value.decrypt_with`` is set.
    * Hash input: the raw ``value.value`` as a string. For ``decryptWith`` this
      is the **ciphertext** as stored in the JSON config — the hash is NOT
      computed over the decrypted plaintext. For plain ``confidential: true``
      it's the plaintext as stored.
    * Output: ``f"*****{md5(raw).hexdigest()[:5]}"`` (lowercase hex).

    Returns ``None`` when the value should not be redacted.
    """
    if value is None:
        return None
    if not (value.confidential or value.decrypt_with):
        return None
    raw = value.value
    digest = hashlib.md5(str(raw).encode()).hexdigest()[:5]
    return f"{CONFIDENTIAL_PREFIX}{digest}"


def compute_reportable_value_for(value: Value, weighted_value_index: int = -1) -> Optional[str]:
    """Redacted telemetry form for an evaluation result's value.

    For a ``weighted_values`` Value the SELECTED variant decides: a rollout's
    variants carry their own ``confidential`` / ``decryptWith``, and the outer
    Value never does, so keying off the outer Value alone sent a confidential
    variant (or, for ``decryptWith``, the decrypted secret) to telemetry in
    plaintext (qfg-goi1.2.13). Matches sdk-go, whose ``reportableValueFor``
    sees the selected Value. The hash input is the variant's stored raw value
    (ciphertext for ``decryptWith``), as for a plain value. An outer Value that
    is itself flagged stays redacted.
    """
    if value is not None and value.type == "weighted_values" and weighted_value_index >= 0:
        raw = value.value
        variants = raw.get("weightedValues") if isinstance(raw, dict) else None
        if isinstance(variants, list) and weighted_value_index < len(variants):
            entry = variants[weighted_value_index]
            selected = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(selected, dict):
                redacted = compute_reportable_value(Value.from_dict(selected))
                if redacted is not None:
                    return redacted
    return compute_reportable_value(value)


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


_UNCOERCIBLE = object()
_INT_RE = re.compile(r"^[+-]?\d+$")
_DOUBLE_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


class Resolver:
    def __init__(self, store: "ConfigStore") -> None:
        self.store = store

    def resolve(
        self,
        value: Value,
        contexts: Contexts,
        config_key: str = "",
        weighted_value_index: int = -1,
        value_type: str = "",
        stored_form: bool = False,
    ) -> Any:
        """Resolve a Value object to a Python native type.

        ``value_type`` is the config's declared valueType. An ENV_VAR-provided
        value is coerced to it strictly; an uncoercible value raises
        ``QuonfigEnvVarCoerceError`` (qfg-2agi.18).

        ``weighted_value_index`` is the variant the evaluator already picked
        for a weighted value (``EvalResult.weighted_value_index``). When given
        it is served as-is, so the served value always agrees with the index
        reported in details and telemetry. Without a hash property the pick
        is random, so drawing again here would disagree (qfg-3ibo).

        ``stored_form`` (``evaluate_details``, qfg-177f) keeps a duration as
        its ISO 8601 text instead of seconds. The text is still validated, so
        a malformed duration raises ``QuonfigEnvVarCoerceError`` either way."""
        if value is None:
            return None

        raw = value.value
        vtype = value.type

        # Handle provided (ENV_VAR) type
        if vtype == "provided":
            env_var_name = raw.get("lookup", "") if isinstance(raw, dict) else ""
            return self._coerce_env_var(
                self._resolve_provided(raw),
                value_type,
                config_key,
                env_var_name,
                stored_form=stored_form,
            )

        # Handle weighted values
        if vtype == "weighted_values":
            return self._resolve_weighted(
                raw,
                value,
                contexts,
                config_key=config_key,
                weighted_value_index=weighted_value_index,
                value_type=value_type,
                stored_form=stored_form,
            )

        # Handle decryption
        if value.confidential and value.decrypt_with:
            raw = self._decrypt_value(raw, value.decrypt_with, contexts, (config_key,))

        return self._coerce(raw, vtype, config_key, stored_form=stored_form)

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

    def _coerce_env_var(
        self,
        raw: Any,
        value_type: str,
        config_key: str = "",
        env_var_name: str = "",
        stored_form: bool = False,
    ) -> Any:
        """Strictly coerce an ENV_VAR string to the config's valueType.

        The error message names the variable and the config but never includes
        the raw value: env vars often carry secrets. Wording is the cross-SDK
        template pinned in integration-test-data (qfg-goi1.10.22).
        """
        if not isinstance(raw, str) or value_type in ("", "string", "json"):
            return raw

        coerced: Any = _UNCOERCIBLE
        if value_type == "bool":
            lowered = raw.lower()
            if lowered == "true":
                coerced = True
            elif lowered == "false":
                coerced = False
        elif value_type == "int":
            if _INT_RE.match(raw):
                coerced = int(raw)
        elif value_type == "double":
            if _DOUBLE_RE.match(raw):
                coerced = float(raw)
        elif value_type == "string_list":
            coerced = [] if raw == "" else [part.strip() for part in raw.split(",")]
        elif value_type == "duration":
            ms = parse_duration_millis(raw)
            if ms is not None:
                coerced = raw if stored_form else ms / 1000
        elif value_type == "log_level":
            coerced = raw.upper()
        else:
            coerced = raw

        if coerced is _UNCOERCIBLE:
            raise QuonfigEnvVarCoerceError(
                f'environment variable "{env_var_name}" cannot be converted to '
                f'{value_type} for config "{config_key}"'
            )
        return coerced

    def _resolve_weighted(
        self,
        raw: Any,
        value: Value,
        contexts: Contexts,
        config_key: str = "",
        weighted_value_index: int = -1,
        value_type: str = "",
        stored_form: bool = False,
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

        if 0 <= weighted_value_index < len(weighted_values):
            return self._resolve_selected(
                weighted_values[weighted_value_index].get("value"),
                contexts,
                config_key,
                value_type,
                stored_form=stored_form,
            )

        # Determine hash fraction
        from .context import get_context_value

        fraction: float
        if hash_by:
            hash_value, found = get_context_value(contexts, hash_by)
            if not (found and hash_value is not None):
                # Missing hash property -> hash an empty value (qfg-9dxb.8).
                # Must agree with Evaluator._weighted_index.
                hash_value = ""
            # Hash input: configKey + contextValue (matches Go/Node SDK)
            to_hash = f"{config_key}{hash_value}"
            uint32_val = mmh3.hash(to_hash, signed=False)
            fraction = uint32_val / _MAX_UINT32
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

        return self._resolve_selected(
            selected, contexts, config_key, value_type, stored_form=stored_form
        )

    def _resolve_selected(
        self,
        selected: Any,
        contexts: Contexts,
        config_key: str,
        value_type: str = "",
        stored_form: bool = False,
    ) -> Any:
        if selected is None:
            return None

        # Recursively resolve the selected value. Pass the config's value type
        # so an ENV_VAR variant is coerced like a non-weighted one
        # (qfg-goi1.10.6, matches sdk-go).
        if isinstance(selected, dict):
            inner_value = Value.from_dict(selected)
            return self.resolve(
                inner_value,
                contexts,
                config_key=config_key,
                value_type=value_type,
                stored_form=stored_form,
            )
        return selected

    def _decrypt_value(
        self,
        raw: Any,
        decrypt_with_key: str,
        contexts: Contexts,
        key_path: Tuple[str, ...] = (),
    ) -> Any:
        """Look up the encryption key config, evaluate it, and decrypt.

        The key config is evaluated through the normal evaluator for the
        client's effective environment, with criteria honored: env rules
        first, then default (qfg-goi1.2.25). It used to take the first valued
        rule across EVERY environment, so a non-production client could pick
        the production key. Mirrors sdk-go ``resolveDecryption``.
        """
        from .crypto import decrypt
        from .evaluator import Evaluator

        if decrypt_with_key in key_path:
            raise QuonfigDecryptionError(
                f"Encryption key config '{decrypt_with_key}' is part of a decryptWith cycle"
            )
        key_config = self.store.get(decrypt_with_key)
        if key_config is None:
            raise QuonfigDecryptionError(
                f"Encryption key config '{decrypt_with_key}' not found in store"
            )
        key_result = Evaluator(self.store).evaluate(decrypt_with_key, contexts)
        key_value = self._resolve_key_value(
            key_result.value, contexts, key_path + (decrypt_with_key,)
        )
        if not isinstance(key_value, str) or key_value == "":
            raise QuonfigDecryptionError(
                f"Could not retrieve encryption key from '{decrypt_with_key}'"
            )
        if not isinstance(raw, str):
            raise QuonfigDecryptionError("Encrypted value must be a string")
        return decrypt(raw, key_value)

    def _resolve_key_value(
        self, value: Optional[Value], contexts: Contexts, key_path: Tuple[str, ...]
    ) -> Any:
        """Resolve the matched key-config value: a plain string, an ENV_VAR,
        or itself an encrypted value (cycle-guarded by ``key_path``)."""
        if value is None:
            return None
        if value.type == "provided":
            try:
                return self._resolve_provided(value.value)
            except QuonfigEnvVarNotSetError:
                return None
        if value.confidential and value.decrypt_with:
            return self._decrypt_value(value.value, value.decrypt_with, contexts, key_path)
        return value.value

    def _coerce(self, raw: Any, vtype: str, config_key: str = "", stored_form: bool = False) -> Any:
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
            # dict / list / int / float / bool (and None handled above) all pass
            # through. Return a copy, never the store's object, so a caller
            # mutation cannot change the config for later reads
            # (qfg-goi1.10.5, sdk-go 543ee9c).
            if isinstance(raw, (dict, list)):
                return copy.deepcopy(raw)
            return raw

        # Durations follow the decided grammar (qfg-2agi decision 1). A
        # malformed stored value raises the same coercion error as a malformed
        # ENV_VAR one (decision 3), outside the try/except below so it is
        # never swallowed into the raw string. The message omits the value.
        if vtype == "duration":
            ms = parse_duration_millis(raw) if isinstance(raw, str) else None
            if ms is None:
                raise QuonfigEnvVarCoerceError(
                    f"Stored value for '{config_key}' could not be coerced to duration"
                )
            return raw if stored_form else ms / 1000

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

            else:
                # Unknown type — return as-is
                return raw
        except Exception:
            return raw
