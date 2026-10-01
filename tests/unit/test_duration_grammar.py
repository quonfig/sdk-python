"""Stored-path durations follow the decided grammar (qfg-2agi.11).

Grammar and millis come from integration-test-data/tests/duration/grammar.yaml
(qfg-2agi.29), mirrored here because the SDK has no YAML dependency:
fractions on S only, at least one component, no dangling T, at most 9
fractional digits, magnitude <= P36500D, full-string match on ASCII digits.
Millis are exact decimal, rounded half up.

Malformed stored value contract (decision 3): typed getter with a default
returns the default and warns once per key; no default returns None;
on_no_default="error" raises QuonfigEnvVarCoerceError; details report ERROR;
the raw value never reaches a log line or error message.
"""

from __future__ import annotations

import decimal
import logging
from typing import Any

import pytest

from quonfig import Quonfig
from quonfig.duration import parse_duration_millis
from quonfig.evaluator import Evaluator
from quonfig.exceptions import QuonfigEnvVarCoerceError
from quonfig.types import ConfigEnvelope, ConfigResponse, Meta

VALID = [
    ("PT0S", 0),
    ("P0D", 0),
    ("P1D", 86400000),
    ("PT1H", 3600000),
    ("PT1M", 60000),
    ("PT1S", 1000),
    ("PT05S", 5000),
    ("PT90S", 90000),
    ("PT1H30M", 5400000),
    ("P1DT6H2M1.5S", 108121500),
    ("PT30M", 1800000),
    ("PT0.2S", 200),
    ("PT1.5S", 1500),
    ("PT2.01S", 2010),
    ("PT1.005S", 1005),
    ("PT0.0005S", 1),
    ("PT0.0004S", 0),
    ("PT0.123456789S", 123),
    ("PT0.999999999S", 1000),
    ("P36500D", 3153600000000),
    ("PT876000H", 3153600000000),
]

INVALID = [
    "",
    "P",
    "PT",
    "P1DT",
    "PT0.5H",
    "PT1.5M",
    "P0.5D",
    "P1W",
    "P1Y",
    "P1M",
    "-PT5S",
    "PT-5S",
    "pt5s",
    " PT5S",
    "PT5S ",
    "PT.5S",
    "PT5.S",
    "PT1,5S",
    "PT0.1234567890S",
    "PT5M3H",
    "PT5S5S",
    "P36501D",
    "P36500DT1S",
    "PT99999999999999999999S",
    "PT5S\n",
    "garbage\nPT5S",
    "PT٥S",
    "30s",
    "5m",
    "1h30m",
    "30",
    "garbage",
    # Beyond Decimal's 28-digit default precision: must not raise from quantize.
    "PT" + "9" * 40 + "S",
    "P" + "9" * 30 + "D",
]


@pytest.mark.parametrize("text,millis", VALID)
def test_grammar_valid(text: str, millis: int) -> None:
    assert parse_duration_millis(text) == millis


@pytest.mark.parametrize("text", INVALID)
def test_grammar_invalid(text: str) -> None:
    assert parse_duration_millis(text) is None


def _client(raw: Any, on_no_default: str = "warn") -> Quonfig:
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment="Production",
        on_init_failure="return_zero_value",
        on_no_default=on_no_default,
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    config = ConfigResponse.from_dict(
        {
            "id": "id-d.key",
            "key": "d.key",
            "type": "config",
            "valueType": "duration",
            "sendToClientSdk": False,
            "environments": [],
            "default": {
                "rules": [
                    {
                        "criteria": [{"operator": "ALWAYS_TRUE"}],
                        "value": {"type": "duration", "value": raw},
                    }
                ]
            },
        }
    )
    c._store.update(
        ConfigEnvelope(configs=[config], meta=Meta(version="test", environment="Production"))
    )
    c._evaluator = Evaluator(c._store, "Production")
    c._initialized.set()
    return c


@pytest.mark.parametrize("text,millis", VALID)
def test_stored_valid_returns_seconds(text: str, millis: int) -> None:
    c = _client(text)
    assert c.get_duration("d.key", default=-1.0) == millis / 1000
    assert c.get("d.key") == millis / 1000


STORED_MALFORMED: list[Any] = [*INVALID, 30, 1.5, "nan", "inf", "1e3"]


@pytest.mark.parametrize("raw", STORED_MALFORMED)
def test_stored_malformed_returns_default_and_warns_once(caplog, raw: Any) -> None:
    c = _client(raw)
    with caplog.at_level(logging.WARNING, logger="quonfig"):
        assert c.get_duration("d.key", default=9.0) == 9.0
        assert c.get_duration("d.key", default=9.0) == 9.0
        assert c.get("d.key", default=9.0) == 9.0
    logs = [r for r in caplog.records if "could not be coerced" in r.getMessage()]
    assert len(logs) == 1
    if isinstance(raw, str) and raw.strip():
        assert raw not in logs[0].getMessage()


@pytest.mark.parametrize("raw", STORED_MALFORMED)
def test_stored_malformed_no_default_returns_none(raw: Any) -> None:
    c = _client(raw, on_no_default="ignore")
    assert c.get_duration("d.key") is None


@pytest.mark.parametrize("raw", STORED_MALFORMED)
def test_stored_malformed_get_or_raise_raises_coerce_error(raw: Any) -> None:
    c = _client(raw, on_no_default="error")
    with pytest.raises(QuonfigEnvVarCoerceError):
        c.get_duration("d.key")


@pytest.mark.parametrize("raw", ["30s", "PT0.5H", "P1DT", "garbage", "PT" + "9" * 40 + "S"])
def test_stored_malformed_details_reports_error(raw: str) -> None:
    c = _client(raw)
    d = c.get_json_details("d.key")
    assert d.reason == "ERROR"
    assert d.value is None
    assert raw not in (d.error_message or "")


# The parser must not depend on the caller's thread-local decimal context: a
# host app that lowers precision or traps Inexact/Rounded must still get exact
# millis (or None), never a wrong value or a decimal exception.
_HOSTILE_CONTEXTS = {
    "low_prec": dict(prec=6),
    "round_down": dict(rounding=decimal.ROUND_DOWN),
    "traps_inexact_rounded": dict(
        traps=[decimal.Inexact, decimal.Rounded, decimal.InvalidOperation]
    ),
    "tiny_exponent_range": dict(Emax=10, Emin=-10),
}

_HOSTILE_VALID = [
    ("PT1.005S", 1005),
    ("PT1.5S", 1500),
    ("PT0.0005S", 1),
    ("P1DT6H2M1.5S", 108121500),
    ("PT12345.678901S", 12345679),
    ("P36499DT23H59M59.999999999S", 3153599999999 + 1),
    ("P36500D", 3153600000000),
]

_HOSTILE_INVALID = [
    "P36501D",
    "PT" + "9" * 40 + "S",
    "P" + "9" * 30 + "D",
]


@pytest.mark.parametrize("ctx_name", sorted(_HOSTILE_CONTEXTS))
def test_parse_ignores_caller_decimal_context(ctx_name: str) -> None:
    with decimal.localcontext() as ctx:
        for attr, value in _HOSTILE_CONTEXTS[ctx_name].items():
            if attr == "traps":
                for sig in value:
                    ctx.traps[sig] = True
            else:
                setattr(ctx, attr, value)
        before = (ctx.prec, ctx.rounding, ctx.Emax, ctx.Emin, dict(ctx.traps), dict(ctx.flags))
        for text, millis in _HOSTILE_VALID:
            assert parse_duration_millis(text) == millis, text
        for text in _HOSTILE_INVALID:
            assert parse_duration_millis(text) is None, text
        current = decimal.getcontext()
        after = (
            current.prec,
            current.rounding,
            current.Emax,
            current.Emin,
            dict(current.traps),
            dict(current.flags),
        )
        assert after == before


# Range check runs before rounding: a value a hair over P36500D is rejected
# rather than rounded down onto the ceiling (matches sdk-go and sdk-node).
@pytest.mark.parametrize("text", ["P36500DT0.0001S", "P36500DT0.000000001S", "PT876000H0M0.4S"])
def test_just_over_ceiling_rejected_not_rounded_onto_it(text: str) -> None:
    assert parse_duration_millis(text) is None
