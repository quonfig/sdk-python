from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

import mmh3

from .context import get_context_value
from .operators import evaluate_operator
from .reason import compute_telemetry_reason
from .types import Contexts, Criterion, EvalResult, Rule

if TYPE_CHECKING:
    from .store import ConfigStore

_MAX_UINT32 = 4_294_967_295.0


class Evaluator:
    def __init__(self, store: "ConfigStore", environment_id: str) -> None:
        self.store = store
        self.environment_id = environment_id

    def evaluate(self, key: str, contexts: Contexts) -> EvalResult:
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
        matching_env = None
        for env in config.environments:
            if env.id == self.environment_id:
                matching_env = env
                break
        if (
            matching_env is None
            and config.environment
            and config.environment.id == self.environment_id
        ):
            matching_env = config.environment

        if matching_env is not None:
            for idx, rule in enumerate(matching_env.rules):
                if self._rule_matches(rule, contexts):
                    wv_idx = self._weighted_index(rule, contexts, key)
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
                    )

        for idx, rule in enumerate(config.default.rules):
            if self._rule_matches(rule, contexts):
                wv_idx = self._weighted_index(rule, contexts, key)
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

    def _weighted_index(self, rule: Rule, contexts: Contexts, config_key: str) -> int:
        """Return the selected weighted value index, or -1 if not a weighted value."""
        if rule.value is None or rule.value.type != "weighted_values":
            return -1
        raw = rule.value.value
        if not isinstance(raw, dict):
            return -1
        weighted_values = raw.get("weightedValues", [])
        hash_by = raw.get("hashByPropertyName", "")
        if not weighted_values:
            return -1

        if hash_by:
            hash_value, found = get_context_value(contexts, hash_by)
            if found and hash_value is not None:
                to_hash = f"{config_key}{hash_value}"
                uint32_val = mmh3.hash(to_hash, signed=False)
                fraction = uint32_val / _MAX_UINT32
            else:
                fraction = random.random()
        else:
            fraction = random.random()

        total_weight = sum(wv.get("weight", 0) for wv in weighted_values)
        if total_weight == 0:
            return -1

        threshold = fraction * total_weight
        running_sum = 0.0
        for i, wv in enumerate(weighted_values):
            running_sum += wv.get("weight", 0)
            if running_sum >= threshold:
                return i
        return 0

    def _rule_matches(self, rule: Rule, contexts: Contexts) -> bool:
        return all(self._criterion_matches(c, contexts) for c in rule.criteria)

    def _criterion_matches(self, criterion: Criterion, contexts: Contexts) -> bool:
        operator = criterion.operator
        if operator == "ALWAYS_TRUE":
            return True
        prop_value, _found = get_context_value(contexts, criterion.property_name or "")
        criterion_value = criterion.value_to_match.value if criterion.value_to_match else None
        return evaluate_operator(operator, prop_value, criterion_value, contexts, self.store)
