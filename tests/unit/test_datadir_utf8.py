"""Datadir reads config files and quonfig.json as UTF-8 whatever the locale
(qfg-goi1.2.27).

``open(path)`` with no ``encoding=`` uses the locale encoding: cp1252 on
Windows, ISO-8859-1 under an explicit Latin-1 locale. A non-ASCII value was
then served as mojibake or the file was skipped. The tests simulate a Latin-1
locale by giving the module an ``open`` whose default encoding is latin-1.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

from quonfig import datadir as datadir_mod
from quonfig.datadir import load_datadir

_VALUE = "café — naïve"
_ENV = "Produção"


@pytest.fixture
def latin1_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = builtins.open

    def latin1_default_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if "b" not in mode and not args and "encoding" not in kwargs:
            kwargs["encoding"] = "latin-1"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(datadir_mod, "open", latin1_default_open, raising=False)


def _write(base: Path, env: str = _ENV) -> str:
    (base / "configs").mkdir(parents=True)
    cfg = {
        "id": "1",
        "key": "greeting",
        "type": "config",
        "valueType": "string",
        "default": {
            "rules": [
                {
                    "criteria": [{"operator": "ALWAYS_TRUE"}],
                    "value": {"type": "string", "value": _VALUE},
                }
            ]
        },
    }
    (base / "configs" / "greeting.json").write_bytes(
        json.dumps(cfg, ensure_ascii=False).encode("utf-8")
    )
    (base / "quonfig.json").write_bytes(
        json.dumps({"environments": [env]}, ensure_ascii=False).encode("utf-8")
    )
    return str(base)


def test_config_values_decode_as_utf8(tmp_path: Path, latin1_locale: None) -> None:
    envelope = load_datadir(_write(tmp_path, "production"), "production")
    (cfg,) = [c for c in envelope.configs if c.key == "greeting"]
    assert cfg.default.rules[0].value.value == _VALUE


def test_quonfig_json_decodes_as_utf8(tmp_path: Path, latin1_locale: None) -> None:
    envelope = load_datadir(_write(tmp_path), _ENV)
    (cfg,) = [c for c in envelope.configs if c.key == "greeting"]
    assert cfg.default.rules[0].value.value == _VALUE
