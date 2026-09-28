"""Weighted rollout whose hashByPropertyName is missing from the context
(qfg-9dxb.8).

Contract (revised 2026-09-28): a missing hash property hashes configKey + ""
exactly like a present empty-string value, then walks the weights as normal,
so missing and "" land in the same bucket and a zero-weight variant is never
served. ``hashPropertyMissing: True`` in flag metadata and one WARNING per
config key per client only when the property is missing (not for ""). With no
hashByPropertyName configured, every evaluation still picks a random variant,
as in 1.5.0. When the hash property IS present the bucket is unchanged from
1.5.0.
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


def _client(*keys: str, configs=None) -> Quonfig:
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
            configs=configs if configs is not None else [_config(k) for k in (keys or (KEY,))],
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


# What released v1.5.0 serves for a present empty-string tracking_id on this
# fixture (computed on `git archive v1.5.0`): configKey + "" hashes to the
# 97000-weight bucket, value 2.
EMPTY_BUCKET_VALUE = 2


@pytest.mark.parametrize("contexts", MISSING_CONTEXTS)
def test_missing_hash_property_hashes_empty_value(contexts):
    c = _client()
    for _ in range(50):
        assert c.get_int(KEY, contexts=contexts) == EMPTY_BUCKET_VALUE
        d = c.get_int_details(KEY, contexts=contexts)
        assert d.value == EMPTY_BUCKET_VALUE
        assert d.variant == "split:2"
        assert d.flag_metadata is not None
        assert d.flag_metadata.get("hashPropertyMissing") is True


def test_missing_and_empty_land_in_same_bucket():
    c = _client()
    empty = c.get_int(KEY, contexts={"user": {"tracking_id": ""}})
    assert empty == EMPTY_BUCKET_VALUE
    for p in MISSING_CONTEXTS:
        assert c.get_int(KEY, contexts=p.values[0]) == empty


def test_present_empty_string_is_not_flagged_missing(caplog):
    c = _client()
    caplog.set_level(logging.WARNING)
    d = c.get_int_details(KEY, contexts={"user": {"tracking_id": ""}})
    assert d.flag_metadata is not None
    assert "hashPropertyMissing" not in d.flag_metadata
    assert _warnings(caplog) == []


def _zero_first_config(key: str) -> ConfigResponse:
    raw = copy.deepcopy(_WEIGHTED)
    raw["key"] = key
    raw["id"] = f"id-{key}"
    wvs = raw["environments"][0]["rules"][0]["value"]["value"]["weightedValues"]
    wvs[0]["weight"] = 0
    return ConfigResponse.from_dict(raw)


def test_zero_weight_first_variant_never_served_when_missing():
    keys = [f"zero-first-{i}" for i in range(50)]
    c = _client(configs=[_zero_first_config(k) for k in keys])
    for k in keys:
        for p in MISSING_CONTEXTS:
            assert c.get_int(k, contexts=p.values[0]) != 1, k


def _unhashed_config(hash_by) -> ConfigResponse:
    raw = copy.deepcopy(_WEIGHTED)
    value = raw["environments"][0]["rules"][0]["value"]["value"]
    value["weightedValues"] = [
        {"weight": 50, "value": {"type": "int", "value": "10"}},
        {"weight": 50, "value": {"type": "int", "value": "20"}},
    ]
    if hash_by is None:
        del value["hashByPropertyName"]
    else:
        value["hashByPropertyName"] = hash_by
    return ConfigResponse.from_dict(raw)


@pytest.mark.parametrize("hash_by", [None, ""], ids=["unset", "empty-string"])
@pytest.mark.parametrize(
    "contexts", [None, {"user": {"tracking_id": "user-0", "key": "u"}}], ids=["no-ctx", "ctx"]
)
def test_no_hash_property_is_random_per_evaluation(hash_by, contexts, caplog):
    c = _client(configs=[_unhashed_config(hash_by)])
    caplog.set_level(logging.WARNING)
    seen = [c.get_int(KEY, contexts=contexts) for _ in range(1000)]
    assert set(seen) == {10, 20}
    # Roughly 50/50 (p of falling outside 350..650 on a fair coin is ~1e-21).
    assert 350 < seen.count(10) < 650
    d = c.get_int_details(KEY, contexts=contexts)
    assert d.flag_metadata is not None
    assert "hashPropertyMissing" not in d.flag_metadata
    assert _warnings(caplog) == []


# Computed on the released v1.5.0 code (git archive v1.5.0). The same
# (key, tracking_id) must land in the same bucket after this change. Bucket 0
# (value 1) reported variant "static" in v1.5.0; that was the qfg-stbb bug and
# it is now "split:0" -- the bucket itself is unchanged.
PINNED = [
    ("user-0", 2, "split:2"),
    ("user-1", 2, "split:2"),
    ("user-17", 3, "split:1"),
    ("user-61", 3, "split:1"),
    ("user-71", 1, "split:0"),
    ("user-185", 1, "split:0"),
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
            "which is missing from context; hashing an empty value instead",
            f'quonfig: weighted rollout for "{other}" hashes on "user.tracking_id" '
            "which is missing from context; hashing an empty value instead",
        ]
    )


def test_present_property_never_warns(caplog):
    c = _client()
    caplog.set_level(logging.WARNING)
    for i in range(50):
        c.get_int(KEY, contexts={"user": {"tracking_id": f"user-{i}"}})
    assert _warnings(caplog) == []
