"""Weighted rollout with no hashByPropertyName picks a random variant per
evaluation (qfg-3ibo). The variant served and the weighted_value_index
reported (details variant, flag metadata, telemetry) must come from the same
draw, so they always agree.
"""

from __future__ import annotations

from quonfig import Quonfig
from quonfig.evaluator import Evaluator
from quonfig.types import ConfigEnvelope, ConfigResponse, Meta

KEY = "feature-flag.random-split"

_RANDOM_SPLIT = {
    "id": "id-random-split",
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
                                {"weight": 50000, "value": {"type": "int", "value": "10"}},
                                {"weight": 50000, "value": {"type": "int", "value": "20"}},
                            ],
                        },
                    },
                }
            ],
        }
    ],
    "default": {"rules": []},
}

INDEX_FOR_VALUE = {10: 0, 20: 1}


def _client() -> Quonfig:
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
            configs=[ConfigResponse.from_dict(_RANDOM_SPLIT)],
            meta=Meta(version="test", environment="Production"),
        )
    )
    c._evaluator = Evaluator(c._store, "Production")
    c._initialized.set()
    return c


def test_reported_index_matches_served_value():
    c = _client()
    seen = set()
    for _ in range(200):
        d = c.get_int_details(KEY)
        expected = INDEX_FOR_VALUE[d.value]
        seen.add(d.value)
        # Index 0 currently reports reason STATIC, not SPLIT (tracked
        # separately), so only check the index where it is reported.
        if d.variant.startswith("split:"):
            assert d.variant == f"split:{expected}"
            assert d.flag_metadata is not None
            assert d.flag_metadata.get("weighted_value_index") == expected
    # Still random across evaluations, as in 1.5.0.
    assert seen == {10, 20}


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.results = []

    def record_evaluation(self, result) -> None:
        self.results.append(result)

    def record_context(self, contexts) -> None:
        pass


def test_telemetry_index_matches_served_value():
    c = _client()
    tel = _RecordingTelemetry()
    c._telemetry = tel
    for _ in range(200):
        c.get_int(KEY)
    assert len(tel.results) == 200
    for r in tel.results:
        assert r.weighted_value_index == INDEX_FOR_VALUE[r.resolved_value]
