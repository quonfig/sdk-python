"""Integration-test environment setup.

The shared YAML fixture corpus (integration-test-data) references a small set
of environment variables — keep them set for the whole pytest session
regardless of which subset of integration test files is being run. Without
this, running e.g. ``pytest tests/integration/test_get_or_raise.py`` in
isolation surfaces spurious ``QuonfigEnvVarNotSetError``s because the env
vars are otherwise only seeded as a side-effect of importing
``telemetry_helpers`` / ``aggregator_helpers``.
"""

from __future__ import annotations

import os


def _seed_integration_env() -> None:
    os.environ.setdefault(
        "PREFAB_INTEGRATION_TEST_ENCRYPTION_KEY",
        "c87ba22d8662282abe8a0e4651327b579cb64a454ab0f4c170b45b15f049a221",
    )
    os.environ.setdefault("IS_A_NUMBER", "1234")
    os.environ.setdefault("NOT_A_NUMBER", "not_a_number")
    os.environ.pop("MISSING_ENV_VAR", None)


_seed_integration_env()
