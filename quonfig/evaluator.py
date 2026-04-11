from __future__ import annotations

from typing import TYPE_CHECKING

from .types import ConfigResponse, Criterion, EvalResult, Rule, Contexts
from .context import get_context_value
from .operators import evaluate_operator

if TYPE_CHECKING:
    from .store import ConfigStore


class Evaluator:
    def __init__(self, store: "ConfigStore", environment_id: str) -> None:
        self.store = store
        self.environment_id = environment_id  # e.g. "Production"

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

        # Try environment-specific rules first — search all environments for a match
        matching_env = None
        for env in config.environments:
            if env.id == self.environment_id:
                matching_env = env
                break
        # Fallback to singular .environment for backward compat
        if matching_env is None and config.environment and config.environment.id == self.environment_id:
            matching_env = config.environment

        if matching_env is not None:
            for idx, rule in enumerate(matching_env.rules):
                if self._rule_matches(rule, contexts):
                    return EvalResult(
                        value=rule.value,
                        raw_value=rule.value,
                        value_type=config.value_type,
                        reason="RULE_MATCH",
                        row_index=idx,
                        config_id=config.id,
                        config_key=key,
                    )

        # Fall through to default rules
        for idx, rule in enumerate(config.default.rules):
            if self._rule_matches(rule, contexts):
                return EvalResult(
                    value=rule.value,
                    raw_value=rule.value,
                    value_type=config.value_type,
                    reason="DEFAULT",
                    row_index=idx,
                    config_id=config.id,
                    config_key=key,
                )

        return EvalResult(
            value=None,
            raw_value=None,
            value_type=config.value_type,
            reason="MISSING",
            row_index=None,
            config_id=config.id,
            config_key=key,
        )

    def _rule_matches(self, rule: Rule, contexts: Contexts) -> bool:
        return all(self._criterion_matches(c, contexts) for c in rule.criteria)

    def _criterion_matches(self, criterion: Criterion, contexts: Contexts) -> bool:
        operator = criterion.operator
        if operator == "ALWAYS_TRUE":
            return True
        prop_value, _found = get_context_value(contexts, criterion.property_name or "")
        criterion_value = criterion.value_to_match.value if criterion.value_to_match else None
        return evaluate_operator(operator, prop_value, criterion_value, contexts, self.store)
