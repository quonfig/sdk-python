"""Regression test for qfg-l3xp: the legacy ``prefab_api_url`` kwarg from
the Prefab fork must NOT be accepted by ``Quonfig.__init__``. URL overrides
go through ``api_urls`` only.
"""

from __future__ import annotations

import inspect

import pytest

from quonfig import Quonfig


def test_prefab_api_url_kwarg_rejected() -> None:
    with pytest.raises(TypeError, match="prefab_api_url"):
        Quonfig(prefab_api_url="https://app.staging-prefab.cloud")  # type: ignore[call-arg]


def test_prefab_api_url_not_in_signature() -> None:
    params = inspect.signature(Quonfig.__init__).parameters
    assert "prefab_api_url" not in params
