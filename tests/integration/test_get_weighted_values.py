# AUTO-GENERATED from integration-test-data/tests/eval/get_weighted_values.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

import os

import pytest

from quonfig import Quonfig

DATADIR = os.path.join(
    os.path.dirname(__file__),
    "../../../integration-test-data/data/integration-tests",
)


@pytest.fixture(scope="module")
def config_client():
    os.environ.setdefault(
        "PREFAB_INTEGRATION_TEST_ENCRYPTION_KEY",
        "c87ba22d8662282abe8a0e4651327b579cb64a454ab0f4c170b45b15f049a221",
    )
    os.environ.setdefault("IS_A_NUMBER", "1234")
    os.environ.setdefault("NOT_A_NUMBER", "not_a_number")
    os.environ.pop("MISSING_ENV_VAR", None)
    c = Quonfig(
        datadir=DATADIR,
        environment="Production",
        on_init_failure="return_zero_value",
    )
    c.init()
    return c


# weighted value is consistent 1
def test_weighted_value_is_consistent_1(config_client) -> None:
    c = config_client
    result = c.get_int("feature-flag.weighted", contexts={"user": {"tracking_id": "a72c15f5"}})
    assert result == 1


# weighted value is consistent 2
def test_weighted_value_is_consistent_2(config_client) -> None:
    c = config_client
    result = c.get_int("feature-flag.weighted", contexts={"user": {"tracking_id": "92a202f2"}})
    assert result == 2


# weighted value is consistent 3
def test_weighted_value_is_consistent_3(config_client) -> None:
    c = config_client
    result = c.get_int("feature-flag.weighted", contexts={"user": {"tracking_id": "8f414100"}})
    assert result == 3


# even split ones serves first variant at low hash fraction
def test_even_split_ones_serves_first_variant_at_low_hash_fraction(config_client) -> None:
    c = config_client
    result = c.get_string(
        "feature-flag.weighted.even-split-ones", contexts={"user": {"tracking_id": "b7ff78c8"}}
    )
    assert result == "a"


# even split ones serves first variant at low hash fraction 2
def test_even_split_ones_serves_first_variant_at_low_hash_fraction_2(config_client) -> None:
    c = config_client
    result = c.get_string(
        "feature-flag.weighted.even-split-ones", contexts={"user": {"tracking_id": "289f4748"}}
    )
    assert result == "a"


# even split ones serves second variant at high hash fraction
def test_even_split_ones_serves_second_variant_at_high_hash_fraction(config_client) -> None:
    c = config_client
    result = c.get_string(
        "feature-flag.weighted.even-split-ones", contexts={"user": {"tracking_id": "d60b2cb6"}}
    )
    assert result == "b"


# even split ones serves second variant at high hash fraction 2
def test_even_split_ones_serves_second_variant_at_high_hash_fraction_2(config_client) -> None:
    c = config_client
    result = c.get_string(
        "feature-flag.weighted.even-split-ones", contexts={"user": {"tracking_id": "21bcfd13"}}
    )
    assert result == "b"


# non-standard sum still serves normalized true bucket
def test_non_standard_sum_still_serves_normalized_true_bucket(config_client) -> None:
    c = config_client
    result = c.get_bool(
        "feature-flag.weighted.non-standard", contexts={"user": {"tracking_id": "ff8adf17"}}
    )
    assert result is True


# non-standard sum still serves normalized true bucket 2
def test_non_standard_sum_still_serves_normalized_true_bucket_2(config_client) -> None:
    c = config_client
    result = c.get_bool(
        "feature-flag.weighted.non-standard", contexts={"user": {"tracking_id": "36ef1a7a"}}
    )
    assert result is True


# non-standard sum still serves normalized false bucket
def test_non_standard_sum_still_serves_normalized_false_bucket(config_client) -> None:
    c = config_client
    result = c.get_bool(
        "feature-flag.weighted.non-standard", contexts={"user": {"tracking_id": "f667c76a"}}
    )
    assert result is False


# non-standard sum still serves normalized false bucket 2
def test_non_standard_sum_still_serves_normalized_false_bucket_2(config_client) -> None:
    c = config_client
    result = c.get_bool(
        "feature-flag.weighted.non-standard", contexts={"user": {"tracking_id": "7467ca21"}}
    )
    assert result is False


# weighted value with hash property missing from context hashes empty string
def test_weighted_value_with_hash_property_missing_from_context_hashes_empty_string(
    config_client,
) -> None:
    c = config_client
    result = c.get_int(
        "feature-flag.weighted.missing-hash", contexts={"user": {"key": "no-tracking-id-user"}}
    )
    assert result == 2


# weighted value with no context hashes empty string
def test_weighted_value_with_no_context_hashes_empty_string(config_client) -> None:
    c = config_client
    result = c.get_int("feature-flag.weighted.missing-hash")
    assert result == 2


# weighted value with hash property empty string hashes empty string
def test_weighted_value_with_hash_property_empty_string_hashes_empty_string(config_client) -> None:
    c = config_client
    result = c.get_int(
        "feature-flag.weighted.missing-hash",
        contexts={"user": {"key": "empty-tracking-id-user", "tracking_id": ""}},
    )
    assert result == 2


# weighted value with zero-weight first variant and hash property missing never serves zero-weight variant
def test_weighted_value_with_zero_weight_first_variant_and_hash_property_missing_never_serves_zero_weight_variant(
    config_client,
) -> None:
    c = config_client
    result = c.get_int(
        "feature-flag.weighted.zero-first", contexts={"user": {"key": "no-tracking-id-user"}}
    )
    assert result == 2


# weighted value with no hash property is random on every evaluation
def test_weighted_value_with_no_hash_property_is_random_on_every_evaluation(config_client) -> None:
    c = config_client
    seen = {c.get_int("feature-flag.weighted.no-hash") for _ in range(200)}
    assert seen == {1, 2}, f"values seen over 200 evaluations: {seen}"


# weighted value with no hash property is random on every evaluation with context
def test_weighted_value_with_no_hash_property_is_random_on_every_evaluation_with_context(
    config_client,
) -> None:
    c = config_client
    seen = {
        c.get_int(
            "feature-flag.weighted.no-hash",
            contexts={"user": {"key": "same-user-every-time", "tracking_id": "same-tracking-id"}},
        )
        for _ in range(200)
    }
    assert seen == {1, 2}, f"values seen over 200 evaluations: {seen}"
