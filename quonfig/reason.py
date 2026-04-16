from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import ConfigResponse

REASON_UNKNOWN = 0
REASON_STATIC = 1
REASON_TARGETING_MATCH = 2
REASON_SPLIT = 3
REASON_DEFAULT = 4
REASON_ERROR = 5


def has_targeting_rules(config: "ConfigResponse") -> bool:
    def _check(rules):
        return any(
            c.operator != "ALWAYS_TRUE"
            for rule in rules
            for c in rule.criteria
        )

    if _check(config.default.rules):
        return True
    if config.environment and _check(config.environment.rules):
        return True
    return False


def compute_telemetry_reason(rule_index: int, weighted_value_index: int, config: "ConfigResponse") -> int:
    if weighted_value_index > 0:
        return REASON_SPLIT
    if rule_index == 0 and not has_targeting_rules(config):
        return REASON_STATIC
    return REASON_TARGETING_MATCH


def marshal_selected_value(value: object) -> dict:
    """Wrap a resolved native value in the telemetry type-tagged dict format."""
    if isinstance(value, bool):
        return {"bool": value}
    if isinstance(value, int):
        return {"int": value}
    if isinstance(value, float):
        return {"double": value}
    if isinstance(value, list):
        return {"stringList": value}
    return {"string": str(value) if value is not None else ""}


def field_type_for_value(value: object) -> int:
    """Return the field type code for a context value (matches Go/Node SDK)."""
    if isinstance(value, bool):
        return 5
    if isinstance(value, int):
        return 1
    if isinstance(value, float):
        return 4
    if isinstance(value, list):
        return 10
    return 2  # string
