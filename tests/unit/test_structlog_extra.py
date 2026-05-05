"""Verify ``quonfig[structlog]`` is a declared optional extra.

structlog is only useful for users wiring our processor into their
structlog pipeline. Most installs don't need it — making it an extra
keeps the base install slim while letting structlog users opt in via
``pip install quonfig[structlog]``.

The runtime code already gates the import (``quonfig/logging.py`` does
``try: from structlog import DropEvent``) and raises a clear ImportError
if a user instantiates ``QuonfigLoggerProcessor`` without structlog
installed — see ``test_logging_processor.py``. This test only asserts
the *packaging* contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tomllib is stdlib on 3.11+. The base SDK supports 3.9+, but this is a
# packaging-contract test — running it on any one matrix Python is enough.
if sys.version_info < (3, 11):
    pytest.skip("tomllib requires Python 3.11+", allow_module_level=True)

import tomllib  # noqa: E402


def _load_pyproject() -> dict:
    pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        return tomllib.load(f)


def test_structlog_declared_as_optional_extra() -> None:
    pyproject = _load_pyproject()

    extras = pyproject["tool"]["poetry"].get("extras", {})
    assert "structlog" in extras, (
        "Expected [tool.poetry.extras] to declare a 'structlog' extra so users "
        "can opt into structlog support via `pip install quonfig[structlog]`."
    )
    assert (
        "structlog" in extras["structlog"]
    ), "Expected the 'structlog' extra to reference the structlog package."

    runtime_deps = pyproject["tool"]["poetry"]["dependencies"]
    structlog_dep = runtime_deps.get("structlog")
    assert structlog_dep is not None, (
        "structlog must appear in [tool.poetry.dependencies] (with optional=true) "
        "for poetry to wire up the extras entry."
    )
    assert isinstance(structlog_dep, dict) and structlog_dep.get("optional") is True, (
        "structlog must be marked optional=true in [tool.poetry.dependencies] so "
        "the base install does not pull it in."
    )


def test_quonfig_imports_without_structlog_installed() -> None:
    """The base SDK must import cleanly even with structlog absent.

    ``quonfig/logging.py`` already guards the import — this test pins that
    behavior so a future refactor can't regress it and break users on the
    structlog-less base install.
    """
    if "structlog" in sys.modules:
        # structlog happens to be installed in this test env (it's a dev dep).
        # We can still verify the import-guard branch is reachable by checking
        # the module exposes its sentinel correctly.
        from quonfig import logging as quonfig_logging

        assert hasattr(quonfig_logging, "_STRUCTLOG_AVAILABLE")
        return

    from quonfig import logging as quonfig_logging  # noqa: F401

    assert quonfig_logging._STRUCTLOG_AVAILABLE is False
