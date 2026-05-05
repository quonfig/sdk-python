# AUTO-GENERATED from integration-test-data/tests/eval/datadir_environment.yaml. DO NOT EDIT.
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


# datadir with environment option gets environment-specific value
def test_datadir_with_environment_option_gets_environment_specific_value() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production")
    c.init()
    result = c.get_string("james.test.key")
    assert result == "test4"


# datadir with QUONFIG_ENVIRONMENT env var gets environment-specific value
def test_datadir_with_quonfig_environment_env_var_gets_environment_specific_value() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ENVIRONMENT"] = os.environ.get("QUONFIG_ENVIRONMENT")
    os.environ["QUONFIG_ENVIRONMENT"] = "Production"
    try:
        c = Quonfig(datadir=DATADIR)
        c.init()
        result = c.get_string("james.test.key")
        assert result == "test4"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# environment option supersedes QUONFIG_ENVIRONMENT env var
def test_environment_option_supersedes_quonfig_environment_env_var() -> None:
    env_backup: dict[str, str | None] = {}
    env_backup["QUONFIG_ENVIRONMENT"] = os.environ.get("QUONFIG_ENVIRONMENT")
    os.environ["QUONFIG_ENVIRONMENT"] = "nonexistent"
    try:
        c = Quonfig(datadir=DATADIR, environment="Production")
        c.init()
        result = c.get_string("james.test.key")
        assert result == "test4"
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# config without environment override returns default value
def test_config_without_environment_override_returns_default_value() -> None:
    c = Quonfig(datadir=DATADIR, environment="Production")
    c.init()
    result = c.get_string("config.with.only.default.env.row")
    assert result == "hello from no env row"


# datadir without environment fails to init
def test_datadir_without_environment_fails_to_init() -> None:
    c = Quonfig(datadir=DATADIR)
    with pytest.raises(RuntimeError):
        c.init()


# datadir with invalid environment fails to init
def test_datadir_with_invalid_environment_fails_to_init() -> None:
    c = Quonfig(datadir=DATADIR, environment="nonexistent")
    with pytest.raises(RuntimeError):
        c.init()
