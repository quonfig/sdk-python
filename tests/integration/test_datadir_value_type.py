# AUTO-GENERATED from integration-test-data/tests/eval/datadir_value_type.yaml. DO NOT EDIT.
# Regenerate with:
#   cd integration-test-data/generators && npm run generate -- --target=python
# Source: integration-test-data/generators/src/targets/python.ts

from __future__ import annotations

import os

from quonfig import Quonfig

DATADIR = os.path.join(
    os.path.dirname(__file__),
    "../../../integration-test-data/data/integration-tests",
)


# datadir int config value is loaded as a number, not a string
def test_datadir_int_config_value_is_loaded_as_a_number_not_a_string() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production")
    c.init()
    result = c.get_int("brand.new.int")
    assert result == 123
    # raw_config(...) is added under qfg-bwwj; until then this raises AttributeError.
    raw = c.raw_config("brand.new.int")
    assert raw is not None, "raw_config(brand.new.int) should be loaded"
    raw_value = raw.default.rules[0].value.value
    assert isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool), (
        "datadir loader must coerce brand.new.int to a number, "
        f"got {type(raw_value).__name__} ({raw_value!r})"
    )


# datadir double config value is loaded as a number, not a string
def test_datadir_double_config_value_is_loaded_as_a_number_not_a_string() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production")
    c.init()
    result = c.get_float("my-double-key")
    assert abs(result - 9.95) < 1e-9
    # raw_config(...) is added under qfg-bwwj; until then this raises AttributeError.
    raw = c.raw_config("my-double-key")
    assert raw is not None, "raw_config(my-double-key) should be loaded"
    raw_value = raw.default.rules[0].value.value
    assert isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool), (
        "datadir loader must coerce my-double-key to a number, "
        f"got {type(raw_value).__name__} ({raw_value!r})"
    )
