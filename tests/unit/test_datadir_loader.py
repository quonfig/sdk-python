from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from quonfig.datadir import SUBDIRS, load_datadir


def _write_workspace(base: Path, *, with_schema: bool = True) -> None:
    """Build a minimal datadir with one valid config and (optionally) a schema."""
    (base / "configs").mkdir(parents=True)
    (base / "configs" / "valid.json").write_text(
        json.dumps(
            {
                "id": "1",
                "key": "valid.flag",
                "type": "FEATURE_FLAG",
                "valueType": "BOOLEAN",
                "default": {"rules": []},
            }
        )
    )

    if with_schema:
        (base / "schemas").mkdir(parents=True)
        # A real JSON Schema document — has no `key`, `type`, or `valueType`.
        (base / "schemas" / "foo.json").write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": "Foo",
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            )
        )

    # Mark "production" as a valid environment so the env-validation step passes.
    (base / "quonfig.json").write_text(json.dumps({"environments": ["production"]}))


def test_schemas_subdir_not_walked(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """schemas/ files must be ignored — no warnings, no empty-key configs."""
    _write_workspace(tmp_path)

    with caplog.at_level(logging.WARNING, logger="quonfig.datadir"):
        envelope = load_datadir(str(tmp_path), "production")

    # No empty-key configs leaked through the loader.
    assert all(c.key for c in envelope.configs), [c.key for c in envelope.configs]
    # The valid config is still loaded.
    assert any(c.key == "valid.flag" for c in envelope.configs)
    # No warnings emitted — schemas/ wasn't walked at all.
    schema_warnings = [r for r in caplog.records if "schemas" in r.getMessage()]
    assert schema_warnings == [], schema_warnings


def test_schemas_not_in_subdirs() -> None:
    """SUBDIRS must not include 'schemas' (qfg-uzsl, qfg-ib7x)."""
    assert "schemas" not in SUBDIRS


def test_empty_key_config_in_configs_dir_is_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A schema-shaped file dropped into configs/ must not produce an empty-key Config."""
    (tmp_path / "configs").mkdir(parents=True)
    (tmp_path / "configs" / "valid.json").write_text(
        json.dumps(
            {
                "id": "1",
                "key": "valid.flag",
                "type": "FEATURE_FLAG",
                "valueType": "BOOLEAN",
                "default": {"rules": []},
            }
        )
    )
    # Drop a JSON Schema doc into configs/ — it has no `key`.
    (tmp_path / "configs" / "stowaway.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "Stowaway",
                "type": "object",
            }
        )
    )
    (tmp_path / "quonfig.json").write_text(json.dumps({"environments": ["production"]}))

    with caplog.at_level(logging.WARNING, logger="quonfig.datadir"):
        envelope = load_datadir(str(tmp_path), "production")

    # No config with empty key in the returned envelope.
    assert all(c.key for c in envelope.configs), [c.key for c in envelope.configs]
    # The valid config is still loaded.
    assert [c.key for c in envelope.configs] == ["valid.flag"]
