"""Verify the package ships the PEP 561 `py.typed` marker.

Without this marker, downstream users running mypy or pyright treat the
package as untyped — the inline type hints become invisible to type checkers.
"""

from __future__ import annotations

from pathlib import Path

import quonfig


def test_py_typed_marker_present_in_package() -> None:
    package_dir = Path(quonfig.__file__).parent
    marker = package_dir / "py.typed"
    assert marker.is_file(), (
        f"PEP 561 marker missing at {marker}. Without it, mypy/pyright "
        "treat quonfig as untyped and ignore inline type hints."
    )
