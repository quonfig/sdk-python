from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Generic, List, Optional, TypeVar, Union

# Type alias for contexts: namespace -> property_name -> value
Contexts = Dict[str, Dict[str, Any]]
ContextValue = Union[str, int, float, bool, list, None]

T = TypeVar("T")


@dataclass
class EvaluationDetails(Generic[T]):
    """Result of a ``get_*_details`` evaluation.

    Includes the resolved value (when available) plus a ``reason`` describing
    how the value was selected, along with optional ``error_code`` /
    ``error_message`` fields populated when ``reason == "ERROR"``.

    ``variant`` and ``flag_metadata`` follow the cross-SDK spec
    (``project/plans/openfeature-resolution-details.md``) — keys use Python's
    snake_case idiom and ``config_type`` values use the wire's snake_case.

    Mirrors the cross-SDK contract for the ``*_details`` API and aligns with
    OpenFeature's ``StandardResolutionReasons`` subset so providers can pass
    the reason through verbatim.
    """

    value: Optional[T]
    # "STATIC" | "TARGETING_MATCH" | "SPLIT" | "DEFAULT" | "ERROR"
    reason: str
    # "FLAG_NOT_FOUND" | "TYPE_MISMATCH" | "GENERAL" — only set when
    # ``reason == "ERROR"``.
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    # OpenFeature variant string. See the cross-SDK spec §2.
    variant: Optional[str] = None
    # OpenFeature flag metadata. See the cross-SDK spec §3.
    flag_metadata: Optional[Dict[str, Any]] = None


# Top-level context name under which Quonfig.should_log(logger_path=...)
# injects the logger path for per-logger rule evaluation. Rules written
# against this context use the property path
# "quonfig-sdk-logging.key". Load-bearing for api-telemetry's example-
# context auto-capture — do NOT rename without updating the matching
# constants in sdk-node, sdk-javascript, sdk-go, and sdk-ruby.
QUONFIG_SDK_LOGGING_CONTEXT_NAME = "quonfig-sdk-logging"
QUONFIG_SDK_LOGGING_CONTEXT_KEY_PROP = "key"


@dataclass
class Value:
    type: str
    value: Any
    confidential: bool = False
    decrypt_with: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Value":
        return cls(
            type=data.get("type", "string"),
            value=data.get("value"),
            confidential=data.get("confidential", False),
            decrypt_with=data.get("decryptWith"),
        )


@dataclass
class Criterion:
    operator: str
    property_name: Optional[str] = None
    value_to_match: Optional[Value] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Criterion":
        vtm = data.get("valueToMatch")
        return cls(
            operator=data["operator"],
            property_name=data.get("propertyName"),
            value_to_match=Value.from_dict(vtm) if vtm else None,
        )


@dataclass
class Rule:
    criteria: List[Criterion]
    value: Optional[Value]

    @classmethod
    def from_dict(cls, data: dict) -> "Rule":
        # `or []` (vs `, [])`) tolerates explicit JSON `null` for these list
        # fields — the api-delivery wire shape sometimes serializes empties
        # as null rather than omitting the key.
        criteria = [Criterion.from_dict(c) for c in (data.get("criteria") or [])]
        v = data.get("value")
        return cls(
            criteria=criteria,
            value=Value.from_dict(v) if v else None,
        )


@dataclass
class RuleSet:
    rules: List[Rule]

    @classmethod
    def from_dict(cls, data: dict) -> "RuleSet":
        rules = [Rule.from_dict(r) for r in (data.get("rules") or [])]
        return cls(rules=rules)


@dataclass
class Environment:
    id: str
    rules: List[Rule]

    @classmethod
    def from_dict(cls, data: dict) -> "Environment":
        rules = [Rule.from_dict(r) for r in (data.get("rules") or [])]
        return cls(
            id=data["id"],
            rules=rules,
        )


@dataclass
class ConfigResponse:
    id: str
    key: str
    type: str
    value_type: str
    send_to_client_sdk: bool
    default: RuleSet
    environment: Optional[Environment] = None
    environments: List[Environment] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "ConfigResponse":
        default_data = data.get("default", {})
        # Support both "default" as RuleSet and legacy format
        if isinstance(default_data, dict):
            default = RuleSet.from_dict(default_data)
        else:
            default = RuleSet(rules=[])

        # Support both singular "environment" and plural "environments" (array)
        environments: List[Environment] = []
        env_list = data.get("environments")
        if isinstance(env_list, list):
            for env_data in env_list:
                if isinstance(env_data, dict):
                    environments.append(Environment.from_dict(env_data))
        elif isinstance(env_list, dict):
            environments.append(Environment.from_dict(env_list))

        # Also support legacy singular "environment" key
        singular_env = data.get("environment")
        if singular_env and isinstance(singular_env, dict):
            environments.append(Environment.from_dict(singular_env))

        # Keep backward-compat: environment points to first env if available
        first_env = environments[0] if environments else None

        return cls(
            id=str(data.get("id", "")),
            key=str(data.get("key", "")),
            type=str(data.get("type", "")),
            value_type=str(data.get("valueType", "")),
            send_to_client_sdk=bool(data.get("sendToClientSdk", False)),
            default=default,
            environment=first_env,
            environments=environments,
        )


@dataclass
class Meta:
    version: str
    environment: str

    @classmethod
    def from_dict(cls, data: dict) -> "Meta":
        return cls(
            version=str(data.get("version", "")),
            environment=str(data.get("environment", "")),
        )


@dataclass
class ConfigEnvelope:
    configs: List[ConfigResponse]
    meta: Meta

    @classmethod
    def from_dict(cls, data: dict) -> "ConfigEnvelope":
        configs = [ConfigResponse.from_dict(c) for c in data.get("configs", [])]
        meta_data = data.get("meta", {})
        meta = Meta.from_dict(meta_data) if meta_data else Meta(version="", environment="")
        return cls(configs=configs, meta=meta)


@dataclass
class EvalResult:
    value: Any
    raw_value: Any
    value_type: str
    reason: str  # "RULE_MATCH" | "DEFAULT" | "MISSING" | "ERROR"
    row_index: Optional[int]
    config_id: Optional[str]
    config_key: str
    # Telemetry fields — set by evaluator + client
    config_type: str = ""
    weighted_value_index: int = -1
    telemetry_reason: int = 0  # 1=STATIC 2=TARGETING_MATCH 3=SPLIT
    resolved_value: Any = None  # set by client after resolver runs
    # Redacted form for telemetry — set when value.confidential or
    # value.decrypt_with is true. Pattern matches Reforge SDK's
    # reportable_value (config_value_unwrapper.py:69-78): the wire
    # selected_value sent to api-telemetry is the redacted string
    # f"*****{md5(raw).hexdigest()[:5]}", while the unredacted resolved
    # value still flows through resolved_value for application use.
    reportable_value: Any = None
