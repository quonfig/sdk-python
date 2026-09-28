from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

import mmh3

from .context import get_context_value
from .operators import evaluate_operator
from .reason import compute_telemetry_reason
from .types import Contexts, Criterion, EvalResult, Rule

if TYPE_CHECKING:
    from .store import ConfigStore

_MAX_UINT32 = 4_294_967_295.0


class Evaluator:
    def __init__(self, store: "ConfigStore", environment_id: str = "") -> None:
        self.store = store
        # NOTE: ``environment_id`` is accepted for back-compat but is NOT used
        # to choose the eval environment. The active env id is ALWAYS the
        # installed envelope's ``meta.environment`` (qfg-pinh). In datadir mode
        # the loader sets ``meta.environment`` to the pinned env, so the pin
        # still selects the env there; in SDK-key delivery mode the server is
        # authoritative and the pin is ignored. Mirrors sdk-go, where eval
        # never branches on the pin — only the datadir loader consumes it.
        self.environment_id = environment_id

    def _effective_environment_id(self) -> str:
        """Resolve the env id to evaluate against.

        Always the installed envelope's ``meta.environment``. In SDK-key
        delivery mode the server is authoritative (any client pin is ignored);
        in datadir mode the loader has already set ``meta.environment`` to the
        pinned env. Mirrors sdk-go's ``c.envID = envelope.Meta.Environment``
        (qfg-pinh).
        """
        return self.store.get_meta_environment()

    def evaluate(self, key: str, contexts: Contexts, _seg_path: Tuple[str, ...] = ()) -> EvalResult:
        # ``_seg_path`` is internal: the keys of the configs being evaluated
        # above this one through IN_SEG / NOT_IN_SEG. With ``key`` appended it
        # lets segment resolution spot a reference cycle (qfg-9dxb.7). It is a
        # path, not a global visited set, so a diamond still resolves.
        seg_path = _seg_path + (key,)
        config = self.store.get(key)
        if config is None:
            return EvalResult(
                value=None,
                raw_value=None,
                value_type="unknown",
                reason="MISSING",
                row_index=None,
                config_id=None,
                config_key=key,
            )

        # Try environment-specific rules first
        env_id = self._effective_environment_id()
        matching_env = None
        for env in config.environments:
            if env.id == env_id:
                matching_env = env
                break
        if matching_env is None and config.environment and config.environment.id == env_id:
            matching_env = config.environment

        if matching_env is not None:
            for idx, rule in enumerate(matching_env.rules):
                if self._rule_matches(rule, contexts, seg_path):
                    wv_idx, hash_missing = self._weighted_index(rule, contexts, key)
                    tr = compute_telemetry_reason(idx, wv_idx, config)
                    return EvalResult(
                        value=rule.value,
                        raw_value=rule.value,
                        value_type=config.value_type,
                        reason="RULE_MATCH",
                        row_index=idx,
                        config_id=config.id,
                        config_key=key,
                        config_type=config.type,
                        weighted_value_index=wv_idx,
                        telemetry_reason=tr,
                        hash_property_missing=hash_missing,
                    )

        for idx, rule in enumerate(config.default.rules):
            if self._rule_matches(rule, contexts, seg_path):
                wv_idx, hash_missing = self._weighted_index(rule, contexts, key)
                tr = compute_telemetry_reason(idx, wv_idx, config)
                return EvalResult(
                    value=rule.value,
                    raw_value=rule.value,
                    value_type=config.value_type,
                    reason="DEFAULT",
                    row_index=idx,
                    config_id=config.id,
                    config_key=key,
                    config_type=config.type,
                    weighted_value_index=wv_idx,
                    telemetry_reason=tr,
                    hash_property_missing=hash_missing,
                )

        return EvalResult(
            value=None,
            raw_value=None,
            value_type=config.value_type,
            reason="MISSING",
            row_index=None,
            config_id=config.id,
            config_key=key,
            config_type=config.type,
        )

    def _weighted_index(self, rule: Rule, contexts: Contexts, config_key: str) -> Tuple[int, bool]:
        """Return ``(index, hash_property_missing)``: the selected weighted
        value index (-1 if not a weighted value), and whether the rollout's
        hashByPropertyName was absent from the context, in which case the
        first variant is used (qfg-9dxb.8)."""
        if rule.value is None or rule.value.type != "weighted_values":
            return -1, False
        raw = rule.value.value
        if not isinstance(raw, dict):
            return -1, False
        weighted_values = raw.get("weightedValues", [])
        hash_by = raw.get("hashByPropertyName", "")
        if not weighted_values:
            return -1, False

        hash_missing = False
        if hash_by:
            hash_value, found = get_context_value(contexts, hash_by)
            if found and hash_value is not None:
                to_hash = f"{config_key}{hash_value}"
                uint32_val = mmh3.hash(to_hash, signed=False)
                fraction = uint32_val / _MAX_UINT32
            else:
                # Missing hash property -> bucket 0 -> first variant.
                fraction = 0.0
                hash_missing = True
        else:
            # No hash property configured -> first variant (Jeff 2026-09-28).
            fraction = 0.0

        total_weight = sum(wv.get("weight", 0) for wv in weighted_values)
        if total_weight == 0:
            return -1, False

        threshold = fraction * total_weight
        running_sum = 0.0
        for i, wv in enumerate(weighted_values):
            running_sum += wv.get("weight", 0)
            if running_sum >= threshold:
                return i, hash_missing
        return 0, hash_missing

    def _rule_matches(self, rule: Rule, contexts: Contexts, seg_path: Tuple[str, ...] = ()) -> bool:
        return all(self._criterion_matches(c, contexts, seg_path) for c in rule.criteria)

    def _criterion_matches(
        self, criterion: Criterion, contexts: Contexts, seg_path: Tuple[str, ...] = ()
    ) -> bool:
        operator = criterion.operator
        if operator == "ALWAYS_TRUE":
            return True
        prop_value, found = get_context_value(contexts, criterion.property_name or "")
        # Type-agnostic presence check: a property is "present" iff the context
        # resolver found the (possibly dotted) path AND the resolved value is
        # not None. Empty string "", 0, and False are intentionally treated as
        # present. Mirrors sdk-go and sdk-node semantics.
        if operator == "IS_PRESENT":
            return found and prop_value is not None
        if operator == "IS_NOT_PRESENT":
            return not (found and prop_value is not None)
        criterion_value = criterion.value_to_match.value if criterion.value_to_match else None
        return evaluate_operator(
            operator, prop_value, criterion_value, contexts, self.store, seg_path=seg_path
        )
