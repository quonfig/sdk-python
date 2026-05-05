"""Unit tests for the ``*_details`` API — focused on edge cases that the
integration suite can't easily synthesize against the shared fixtures
(notably the DEFAULT reason for a flag with rules that all miss).

Builds tiny in-memory ``Quonfig`` clients via the ``make_*`` helpers
in ``tests/conftest.py`` so we can hand-craft configs whose rule sets
fall through entirely.
"""

from __future__ import annotations

from quonfig import EvaluationDetails, Quonfig
from quonfig.evaluator import Evaluator
from quonfig.types import (
    ConfigEnvelope,
    ConfigResponse,
    Criterion,
    Environment,
    Meta,
    Rule,
    RuleSet,
    Value,
)


def _v(t, val):
    return Value(type=t, value=val)


def _crit(op, prop=None, value=None, vtype="string"):
    vtm = _v(vtype, value) if value is not None else None
    return Criterion(operator=op, property_name=prop, value_to_match=vtm)


def _client_with_configs(configs):
    """Hand-stand a Quonfig client whose store is preloaded — bypasses
    init() so we can assert against synthesized rule sets directly."""
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment="Production",
        on_init_failure="return_zero_value",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    envelope = ConfigEnvelope(
        configs=configs,
        meta=Meta(version="test", environment="Production"),
    )
    c._store.update(envelope)
    c._evaluator = Evaluator(c._store, "Production")
    c._initialized.set()
    return c


class TestDefaultReason:
    def test_default_reason_when_all_rules_miss(self):
        """A flag whose rules all check `user.plan == "pro"` and is
        evaluated with no matching context should fall through to the
        DEFAULT reason — flag exists, but nothing matched."""
        config = ConfigResponse(
            id="cfg-1",
            key="needs-pro",
            type="config",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    Rule(
                        criteria=[
                            _crit(
                                "PROP_IS_ONE_OF",
                                "user.plan",
                                ["pro"],
                                vtype="string_list",
                            )
                        ],
                        value=_v("bool", True),
                    )
                ]
            ),
            environment=Environment(id="Production", rules=[]),
        )
        c = _client_with_configs([config])

        details = c.get_bool_details("needs-pro")
        assert isinstance(details, EvaluationDetails)
        assert details.value is None
        assert details.reason == "DEFAULT"
        assert details.error_code is None


class TestErrorReason:
    def test_flag_not_found_returns_error_with_code(self):
        c = _client_with_configs([])
        details = c.get_bool_details("missing.key")
        assert details.value is None
        assert details.reason == "ERROR"
        assert details.error_code == "FLAG_NOT_FOUND"
        assert details.error_message and "missing.key" in details.error_message


class TestSuccessReasons:
    def test_static_reason_when_no_targeting_rules(self):
        """An ALWAYS_TRUE-only config reports STATIC."""
        config = ConfigResponse(
            id="cfg-1",
            key="static.bool",
            type="config",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    Rule(
                        criteria=[_crit("ALWAYS_TRUE")],
                        value=_v("bool", True),
                    )
                ]
            ),
            environment=Environment(
                id="Production",
                rules=[
                    Rule(
                        criteria=[_crit("ALWAYS_TRUE")],
                        value=_v("bool", True),
                    )
                ],
            ),
        )
        c = _client_with_configs([config])

        details = c.get_bool_details("static.bool")
        assert details.value is True
        assert details.reason == "STATIC"
        assert details.error_code is None

    def test_targeting_match_when_property_rule_fires(self):
        config = ConfigResponse(
            id="cfg-2",
            key="targeted.bool",
            type="config",
            value_type="bool",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    Rule(
                        criteria=[
                            _crit(
                                "PROP_IS_ONE_OF",
                                "user.plan",
                                ["pro"],
                                vtype="string_list",
                            )
                        ],
                        value=_v("bool", True),
                    ),
                    Rule(
                        criteria=[_crit("ALWAYS_TRUE")],
                        value=_v("bool", False),
                    ),
                ]
            ),
            environment=Environment(id="Production", rules=[]),
        )
        c = _client_with_configs([config])

        details = c.get_bool_details("targeted.bool", contexts={"user": {"plan": "pro"}})
        assert details.value is True
        assert details.reason == "TARGETING_MATCH"


class TestTypeMismatch:
    def test_bool_details_rejects_string_value(self):
        config = ConfigResponse(
            id="cfg-3",
            key="string.flag",
            type="config",
            value_type="string",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    Rule(
                        criteria=[_crit("ALWAYS_TRUE")],
                        value=_v("string", "not-a-bool"),
                    )
                ]
            ),
            environment=Environment(id="Production", rules=[]),
        )
        c = _client_with_configs([config])

        details = c.get_bool_details("string.flag")
        assert details.value is None
        assert details.reason == "ERROR"
        assert details.error_code == "TYPE_MISMATCH"
        assert details.error_message and "string.flag" in details.error_message

    def test_int_details_coerces_numeric_string(self):
        """Match the existing get_int permissive path — a numeric
        string should coerce, not error."""
        config = ConfigResponse(
            id="cfg-4",
            key="numeric.string",
            type="config",
            value_type="string",
            send_to_client_sdk=True,
            default=RuleSet(
                rules=[
                    Rule(
                        criteria=[_crit("ALWAYS_TRUE")],
                        value=_v("string", "42"),
                    )
                ]
            ),
            environment=Environment(id="Production", rules=[]),
        )
        c = _client_with_configs([config])

        details = c.get_int_details("numeric.string")
        assert details.value == 42
        assert details.reason == "STATIC"
