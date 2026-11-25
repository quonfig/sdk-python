from __future__ import annotations

import copy
import hashlib
import os
import re
from typing import TYPE_CHECKING, Any, Optional

import mmh3

from . import _qf1
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


def compute_reportable_value(value: Value, config_type: str = "") -> Optional[str]:
    """Return the redacted telemetry form for a confidential or secret value.

    Matches Reforge SDK ``reportable_wrapped_value`` (sdk-python
    ``config_value_unwrapper.py:69-78``):

    * Trigger: the config is a secret (``config_type == "secret"``, whether or
      not its value carries ``confidential``), ``value.confidential`` is True,
      or ``value.decrypt_with`` is set.
    * Hash input: the raw ``value.value`` as a string, i.e. the value AS
      STORED. For a secret that is the ``qf1:`` **ciphertext**; the hash is
      never computed over the decrypted plaintext (qf1 spec 13.5). For plain
      ``confidential: true`` it's the plaintext as stored.
    * Output: ``f"*****{md5(raw).hexdigest()[:5]}"`` (lowercase hex).

    Returns ``None`` when the value should not be redacted.
    """
    if value is None:
        return None
    if not (config_type == "secret" or value.confidential or value.decrypt_with):
        return None
    raw = value.value
    digest = hashlib.md5(str(raw).encode()).hexdigest()[:5]
    return f"{CONFIDENTIAL_PREFIX}{digest}"


def compute_reportable_value_for(
    value: Value, weighted_value_index: int = -1, config_type: str = ""
) -> Optional[str]:
    """Redacted telemetry form for an evaluation result's value.

    For a ``weighted_values`` Value the SELECTED variant decides: a rollout's
    variants carry their own ``confidential``, and the outer Value never does,
    so keying off the outer Value alone sent a confidential variant to
    telemetry in plaintext (qfg-goi1.2.13). Matches sdk-go, whose
    ``reportableValueFor`` sees the selected Value. The hash input is the
    variant's stored raw value, as for a plain value. An outer Value that is
    itself flagged stays redacted, and so does every value of a secret.
    """
    if value is not None and value.type == "weighted_values" and weighted_value_index >= 0:
        raw = value.value
        variants = raw.get("weightedValues") if isinstance(raw, dict) else None
        if isinstance(variants, list) and weighted_value_index < len(variants):
            entry = variants[weighted_value_index]
            selected = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(selected, dict):
                redacted = compute_reportable_value(Value.from_dict(selected), config_type)
                if redacted is not None:
                    return redacted
    return compute_reportable_value(value, config_type)


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
        config_type: str = "",
    ) -> Any:
        """Resolve a Value object to a Python native type.

        ``config_type`` is the config's family. A ``secret`` (a document from
        ``secrets/``, or a ``type: "secret"`` wire entry) is decrypted as qf1
        here, lazily, on every get; a keyset problem fails only this read
        (qf1 spec 8, 9.3). A value that still carries ``decryptWith`` always
        fails: that format is no longer read (spec 13.3 R1).

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

        if value.decrypt_with:
            raise QuonfigDecryptionError(
                f"Config '{config_key}' has a value that still carries decryptWith; "
                "this SDK no longer reads that format. Store it in secrets/ as a qf1 secret."
            )

        if config_type == "secret":
            return self._resolve_secret(value, config_key, stored_form=stored_form)

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

        return self._coerce(raw, vtype, config_key, stored_form=stored_form)

    def _resolve_secret(self, value: Value, config_key: str, stored_form: bool = False) -> Any:
        """Decrypt one secret value (qf1 spec 8). A secret holds only string
        rule values (spec 10.2); anything else fails closed rather than being
        served. The error is re-raised OUTSIDE the ``except`` with the config
        key added, so nothing is chained onto it."""
        failure: Optional[_qf1.Qf1Error] = None
        plaintext: Optional[str] = None
        if value.type != "string" or not isinstance(value.value, str):
            failure = _qf1.Qf1Error("malformed", "a secret value must be a qf1 string")
        else:
            try:
                plaintext = _qf1.decrypt(value.value)
            except _qf1.Qf1Error as e:
                failure = e
        if failure is not None or plaintext is None:
            code = failure.code if failure is not None else "decrypt_failed"
            detail = failure.detail if failure is not None else "no value"
            raise _qf1.Qf1Error(code, f"{detail} (config '{config_key}')")
        return self._coerce(plaintext, "string", config_key, stored_form=stored_form)

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
