"""Integration tests for the ``*_details`` API.

The new ``get_*_details`` methods surface the evaluation reason
(STATIC / TARGETING_MATCH / SPLIT / DEFAULT / ERROR) plus an error_code
when relevant. We exercise them against the shared
integration-test-data datadir so the fixtures stay in lockstep with the
sibling SDKs that read the same corpus.
"""

from __future__ import annotations

import os

import pytest

from quonfig import EvaluationDetails, Quonfig

DATADIR = os.path.join(
    os.path.dirname(__file__),
    "../../../integration-test-data/data/integration-tests",
)


@pytest.fixture(scope="module")
def client():
    c = Quonfig(
        datadir=DATADIR,
        environment="Production",
        on_init_failure="return_zero_value",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    c.init()
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Success reasons — STATIC / TARGETING_MATCH / SPLIT
# ---------------------------------------------------------------------------


def test_static_reason_for_always_true_flag(client):
    """A flag with only an ALWAYS_TRUE rule and no targeting should resolve
    with reason=STATIC."""
    details = client.get_bool_details("always.true")
    assert isinstance(details, EvaluationDetails)
    assert details.value is True
    assert details.reason == "STATIC"
    assert details.error_code is None


def test_targeting_match_when_property_rule_hits(client):
    """`of.targeting` resolves true when user.plan is "pro" via a
    PROP_IS_ONE_OF rule — should report TARGETING_MATCH."""
    details = client.get_bool_details(
        "of.targeting", contexts={"user": {"plan": "pro"}}
    )
    assert details.value is True
    assert details.reason == "TARGETING_MATCH"
    assert details.error_code is None


def test_targeting_match_when_property_rule_misses(client):
    """`of.targeting` falls through to the always-true rule (returning
    false) when the user.plan rule doesn't match — that fall-through
    rule still has a property rule above it in the targeting list, so
    the SDK reports TARGETING_MATCH for any rule-driven match."""
    details = client.get_bool_details(
        "of.targeting", contexts={"user": {"plan": "free"}}
    )
    assert details.value is False
    assert details.reason == "TARGETING_MATCH"
    assert details.error_code is None


def test_split_reason_for_weighted_values(client):
    """`of.weighted` is a weighted_values config — should report SPLIT
    when the hash lands on a non-zero weighted index. STATIC fires for
    the first variant since the config has no targeting rules; SPLIT
    fires for any later variant. We sweep a handful of empirically
    chosen ids so at least one produces a SPLIT outcome."""
    saw_split = False
    saw_static = False
    for uid in (
        "user-1", "user-2", "user-3", "user-4", "user-5",
        "u-a", "u-b", "u-c", "u-d", "u-e",
    ):
        details = client.get_string_details(
            "of.weighted", contexts={"user": {"id": uid}}
        )
        assert details.value in ("variant-a", "variant-b")
        # No targeting rules on this config, so the only valid reasons
        # are STATIC (first weighted variant) or SPLIT (any later one).
        assert details.reason in ("STATIC", "SPLIT")
        if details.reason == "SPLIT":
            saw_split = True
        if details.reason == "STATIC":
            saw_static = True
    # `user-2` lands on variant-b (a non-zero weighted index) — pin the
    # SPLIT case on a deterministic id so this assertion can't drift.
    deterministic = client.get_string_details(
        "of.weighted", contexts={"user": {"id": "user-2"}}
    )
    assert deterministic.reason == "SPLIT"
    assert deterministic.value == "variant-b"
    assert saw_split and saw_static, (
        "Expected the sweep to cover both STATIC (variant-a) and "
        "SPLIT (variant-b) outcomes"
    )


# ---------------------------------------------------------------------------
# Error reasons
# ---------------------------------------------------------------------------


def test_flag_not_found_for_missing_key(client):
    details = client.get_bool_details("does.not.exist")
    assert details.value is None
    assert details.reason == "ERROR"
    assert details.error_code == "FLAG_NOT_FOUND"
    assert details.error_message is not None


def test_type_mismatch_when_asking_bool_for_string(client):
    """`my-test-key` is a string config — asking for a bool should fail
    type coercion (string "It's a Test!" can't coerce to bool)."""
    details = client.get_bool_details("my-test-key")
    assert details.value is None
    assert details.reason == "ERROR"
    assert details.error_code == "TYPE_MISMATCH"


def test_type_mismatch_when_asking_int_for_non_numeric_string(client):
    """`my-test-key`'s value is "It's a Test!" — coercion to int fails."""
    details = client.get_int_details("my-test-key")
    assert details.value is None
    assert details.reason == "ERROR"
    assert details.error_code == "TYPE_MISMATCH"


def test_string_details_returns_string_value(client):
    details = client.get_string_details("my-test-key")
    assert details.reason in ("STATIC", "TARGETING_MATCH")
    assert isinstance(details.value, str)


def test_int_details_for_int_config(client):
    details = client.get_int_details("jeffreys.test.int")
    assert details.value is not None
    assert isinstance(details.value, int)
    assert details.reason in ("STATIC", "TARGETING_MATCH")


def test_string_list_details(client):
    details = client.get_string_list_details("my-string-list-key")
    assert details.value is not None
    assert isinstance(details.value, list)
    assert all(isinstance(x, str) for x in details.value)
    assert details.reason in ("STATIC", "TARGETING_MATCH")


def test_json_details_passthrough(client):
    """get_json_details accepts whatever the resolver returns — no
    type coercion."""
    details = client.get_json_details("test.json")
    assert details.reason in ("STATIC", "TARGETING_MATCH")
    # The value should be a parsed dict/list.
    assert details.value is not None


# ---------------------------------------------------------------------------
# Bound client mirrors
# ---------------------------------------------------------------------------


def test_bound_client_get_bool_details_uses_bound_context(client):
    bound = client.with_context({"user": {"plan": "pro"}})
    details = bound.get_bool_details("of.targeting")
    assert details.value is True
    assert details.reason == "TARGETING_MATCH"


def test_bound_client_get_string_details(client):
    bound = client.with_context({})
    details = bound.get_string_details("my-test-key")
    assert details.reason in ("STATIC", "TARGETING_MATCH")
    assert isinstance(details.value, str)
