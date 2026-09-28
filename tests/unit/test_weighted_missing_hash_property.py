"""Weighted rollout whose hashByPropertyName is missing from the context
(qfg-9dxb.8).

Contract: fraction 0.0 -> the FIRST weighted variant (no random bucket),
``hashPropertyMissing: True`` in flag metadata only when that fallback fires,
and one WARNING per config key per client. When the hash property IS present
the bucket must be unchanged from 1.5.0.
"""

from __future__ import annotations

import copy
import logging

import pytest

from quonfig import Quonfig
from quonfig.evaluator import Evaluator
from quonfig.types import ConfigEnvelope, ConfigResponse, Meta

KEY = "feature-flag.weighted"

# Mirrors integration-test-data feature-flags/feature-flag.weighted.json:
# hashes on user.tracking_id; value 1 @ 1000, 3 @ 2000, 2 @ 97000.
_WEIGHTED = {
    "id": "16838163869852699",
    "key": KEY,
    "type": "feature_flag",
    "valueType": "int",
    "sendToClientSdk": False,
    "environments": [
        {
            "id": "Production",
            "rules": [
                {
                    "criteria": [{"operator": "ALWAYS_TRUE"}],
                    "value": {
                        "type": "weighted_values",
                        "value": {
                            "weightedValues": [
                                {"weight": 1000, "value": {"type": "int", "value": "1"}},
                                {"weight": 2000, "value": {"type": "int", "value": "3"}},
                                {"weight": 97000, "value": {"type": "int", "value": "2"}},
                            ],
                            "hashByPropertyName": "user.tracking_id",
                        },
                    },
                }
            ],
        }
    ],
    "default": {
        "rules": [
            {
                "criteria": [{"operator": "ALWAYS_TRUE"}],
                "value": {"type": "int", "value": "0"},
            }
        ]
    },
}


def _config(key: str = KEY) -> ConfigResponse:
    raw = copy.deepcopy(_WEIGHTED)
    raw["key"] = key
    raw["id"] = f"id-{key}"
    return ConfigResponse.from_dict(raw)


def _client(*keys: str) -> Quonfig:
    c = Quonfig(
        sdk_key="",
        datadir=None,
        environment="Production",
        on_init_failure="return_zero_value",
        collect_evaluation_summaries=False,
        context_upload_mode="none",
    )
    c._store.update(
        ConfigEnvelope(
            configs=[_config(k) for k in (keys or (KEY,))],
            meta=Meta(version="test", environment="Production"),
        )
    )
    c._evaluator = Evaluator(c._store, "Production")
    c._initialized.set()
    return c


MISSING_CONTEXTS = [
    pytest.param(None, id="no-context"),
    pytest.param({"team": {"tracking_id": "t-1"}}, id="named-context-missing"),
    pytest.param({"user": {"key": "u-1"}}, id="property-missing"),
    pytest.param({"user": {"tracking_id": None}}, id="property-none"),
]


@pytest.mark.parametrize("contexts", MISSING_CONTEXTS)
def test_missing_hash_property_serves_first_variant(contexts):
    c = _client()
    for _ in range(50):
        assert c.get_int(KEY, contexts=contexts) == 1
        d = c.get_int_details(KEY, contexts=contexts)
        assert d.value == 1
        assert d.flag_metadata is not None
        assert d.flag_metadata.get("hashPropertyMissing") is True


# Computed on the released v1.5.0 code (git archive v1.5.0). The same
# (key, tracking_id) must land in the same bucket after this change.
PINNED = [
    ("user-0", 2, "split:2"),
    ("user-1", 2, "split:2"),
    ("user-17", 3, "split:1"),
    ("user-61", 3, "split:1"),
    ("user-71", 1, "static"),
    ("user-185", 1, "static"),
    ("", 2, "split:2"),
    (0, 2, "split:2"),
    (12345, 2, "split:2"),
    ("abc", 2, "split:2"),
]


@pytest.mark.parametrize("tracking_id,expected,variant", PINNED)
def test_present_hash_property_bucket_unchanged_from_1_5_0(tracking_id, expected, variant):
    c = _client()
    ctx = {"user": {"tracking_id": tracking_id}}
    assert c.get_int(KEY, contexts=ctx) == expected
    d = c.get_int_details(KEY, contexts=ctx)
    assert d.value == expected
    assert d.variant == variant
    assert d.flag_metadata is not None
    assert "hashPropertyMissing" not in d.flag_metadata


def _warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "hashes on" in r.getMessage()
    ]


def test_warns_once_per_config_key(caplog):
    other = "feature-flag.weighted-2"
    c = _client(KEY, other)
    caplog.set_level(logging.WARNING)
    for _ in range(100):
        c.get_int(KEY, contexts={"user": {"key": "u"}})
        c.get_int_details(KEY)
    for _ in range(20):
        c.get_int(other)
    # Present property never warns.
    for _ in range(20):
        c.get_int(KEY, contexts={"user": {"tracking_id": "user-0"}})

    assert sorted(_warnings(caplog)) == sorted(
        [
            f'quonfig: weighted rollout for "{KEY}" hashes on "user.tracking_id" '
            "which is missing from context; using first variant",
            f'quonfig: weighted rollout for "{other}" hashes on "user.tracking_id" '
            "which is missing from context; using first variant",
        ]
    )


def test_present_property_never_warns(caplog):
    c = _client()
    caplog.set_level(logging.WARNING)
    for i in range(50):
        c.get_int(KEY, contexts={"user": {"tracking_id": f"user-{i}"}})
    assert _warnings(caplog) == []


# --- No hash property configured at all (Jeff, 2026-09-28) ---------------
# hashByPropertyName unset or empty -> fraction 0.0 -> the FIRST weighted
# variant on every call (matches sdk-go/node/ruby/net/java). No
# hashPropertyMissing metadata and no warning: there is no property to miss.


def _no_hash_client(hash_by):
    c = _client()
    raw = c._store.get(KEY)
    wv = raw.environments[0].rules[0].value.value
    if hash_by is None:
        wv.pop("hashByPropertyName", None)
    else:
        wv["hashByPropertyName"] = hash_by
    return c


NO_HASH = [pytest.param(None, id="unset"), pytest.param("", id="empty")]
ANY_CONTEXT = [
    pytest.param(None, id="no-context"),
    pytest.param({"user": {"tracking_id": "user-0", "key": "u-1"}}, id="with-context"),
]


@pytest.mark.parametrize("hash_by", NO_HASH)
@pytest.mark.parametrize("contexts", ANY_CONTEXT)
def test_no_hash_property_serves_first_variant(hash_by, contexts, caplog):
    c = _no_hash_client(hash_by)
    caplog.set_level(logging.WARNING)
    for _ in range(50):
        assert c.get_int(KEY, contexts=contexts) == 1
        d = c.get_int_details(KEY, contexts=contexts)
        assert d.value == 1
        assert d.flag_metadata is not None
        assert "hashPropertyMissing" not in d.flag_metadata
    assert _warnings(caplog) == []


# Zero-weight first variant: at fraction 0.0 the running-sum walk
# (running >= threshold, threshold == 0) stops at index 0 even though its
# weight is 0. sdk-net and sdk-java walk identically, so all agree.
@pytest.mark.parametrize(
    "hash_by,contexts",
    [
        pytest.param(None, None, id="no-hash-property"),
        pytest.param("", {"user": {"tracking_id": "x"}}, id="empty-hash-property"),
        pytest.param("user.tracking_id", {"user": {"key": "u"}}, id="missing-property"),
    ],
)
def test_zero_weight_first_variant_is_still_picked_at_fraction_zero(hash_by, contexts):
    c = _no_hash_client(hash_by)
    wv = c._store.get(KEY).environments[0].rules[0].value.value
    wv["weightedValues"][0]["weight"] = 0
    for _ in range(50):
        assert c.get_int(KEY, contexts=contexts) == 1
