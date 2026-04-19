# AUTO-GENERATED from integration-test-data/tests/eval/datadir_environment.yaml
# Do not edit by hand. Regenerate with:
#   python scripts/generate_integration_tests_python.py

import os

import pytest

from quonfig import Quonfig

DATADIR = os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests")


def test_datadir_with_environment_option_gets_environment_specific_value():
    c = Quonfig(datadir=os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests"), environment='Production')
    c.init()
    result = c.get_string('james.test.key')
    assert result == 'test4'


def test_datadir_with_quonfig_environment_env_var_gets_environment_specific_value():
    env_backup = {}
    env_backup['QUONFIG_ENVIRONMENT'] = os.environ.get('QUONFIG_ENVIRONMENT')
    os.environ['QUONFIG_ENVIRONMENT'] = 'Production'
    try:
        c = Quonfig(datadir=os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests"))
        c.init()
        result = c.get_string('james.test.key')
        assert result == 'test4'
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_environment_option_supersedes_quonfig_environment_env_var():
    env_backup = {}
    env_backup['QUONFIG_ENVIRONMENT'] = os.environ.get('QUONFIG_ENVIRONMENT')
    os.environ['QUONFIG_ENVIRONMENT'] = 'nonexistent'
    try:
        c = Quonfig(datadir=os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests"), environment='Production')
        c.init()
        result = c.get_string('james.test.key')
        assert result == 'test4'
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_config_without_environment_override_returns_default_value():
    c = Quonfig(datadir=os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests"), environment='Production')
    c.init()
    result = c.get_string('config.with.only.default.env.row')
    assert result == 'hello from no env row'


def test_datadir_without_environment_fails_to_init():
    c = Quonfig(datadir=os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests"))
    with pytest.raises(RuntimeError):
        c.init()


def test_datadir_with_invalid_environment_fails_to_init():
    c = Quonfig(datadir=os.path.join(os.path.dirname(__file__), "../../../integration-test-data/data/integration-tests"), environment='nonexistent')
    with pytest.raises(RuntimeError):
        c.init()
