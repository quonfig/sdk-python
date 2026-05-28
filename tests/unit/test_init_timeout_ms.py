"""qfg-o8zr: collapse init_timeout + initialization_timeout_sec into a single
``init_timeout_ms`` (milliseconds, default 10_000).

The legacy ``init_timeout`` (seconds, float) and ``initialization_timeout_sec``
(seconds, float) kwargs are kept as deprecated aliases for one minor cycle —
each emits a ``DeprecationWarning`` and is forwarded as ``value * 1000`` into
``_init_timeout_ms``. The canonical kwarg wins when more than one is passed.
"""

from __future__ import annotations

import inspect
import warnings

import pytest

from quonfig import Quonfig


def test_init_timeout_ms_in_signature_with_default_10_000() -> None:
    params = inspect.signature(Quonfig.__init__).parameters
    assert "init_timeout_ms" in params
    assert params["init_timeout_ms"].default == 10_000


def test_init_timeout_ms_stored_as_ms_int() -> None:
    client = Quonfig(sdk_key="sdk-test", api_urls=["http://localhost:0"], init_timeout_ms=2_500)
    assert client._init_timeout_ms == 2_500


def test_init_timeout_ms_default_is_10_000_ms() -> None:
    client = Quonfig(sdk_key="sdk-test", api_urls=["http://localhost:0"])
    assert client._init_timeout_ms == 10_000


def test_deprecated_init_timeout_kwarg_forwards_with_unit_multiplication() -> None:
    with pytest.warns(DeprecationWarning, match="init_timeout"):
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=["http://localhost:0"],
            init_timeout=2.5,
        )
    assert client._init_timeout_ms == 2_500


def test_deprecated_initialization_timeout_sec_kwarg_forwards_with_unit_multiplication() -> None:
    with pytest.warns(DeprecationWarning, match="initialization_timeout_sec"):
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=["http://localhost:0"],
            initialization_timeout_sec=0.5,
        )
    assert client._init_timeout_ms == 500


def test_canonical_init_timeout_ms_wins_over_deprecated_aliases() -> None:
    with warnings.catch_warnings():
        # The aliases still emit warnings, but the canonical kwarg must win.
        warnings.simplefilter("ignore", DeprecationWarning)
        client = Quonfig(
            sdk_key="sdk-test",
            api_urls=["http://localhost:0"],
            init_timeout_ms=7_500,
            init_timeout=99.0,
            initialization_timeout_sec=88.0,
        )
    assert client._init_timeout_ms == 7_500


def test_wait_initialized_uses_init_timeout_ms_in_seconds() -> None:
    """``_wait_initialized`` waits on an Event whose timeout is in seconds; the
    ms value must be divided by 1000 before being passed in. Regression guard
    against accidentally passing the ms value straight through."""
    import time

    client = Quonfig(
        sdk_key="sdk-test",
        api_urls=["http://localhost:0"],
        init_timeout_ms=50,  # 0.05s
        on_init_failure="return_zero_value",
    )
    # _initialized is never set, so _wait_initialized must block ~0.05s, NOT
    # ~50s. We measure the wall-clock cost and require it to be < 1s.
    start = time.monotonic()
    client._wait_initialized()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"_wait_initialized blocked {elapsed:.2f}s — expected < 1s"
