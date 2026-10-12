"""decryptWith key config is evaluated for the client's effective environment
with criteria honored (qfg-goi1.2.25).

Canonical: sdk-go runtime_resolver.go ``resolveDecryption`` evaluates the key
config through the normal evaluator (env rules first, then default). Test shape
follows sdk-net EnvScopedDecryptionKeyTests.cs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from quonfig import Quonfig
from quonfig.crypto import encrypt, generate_new_hex_key

KEY_CONFIG = "quonfig.secrets.encryption.key"


def _env_var_value(lookup: str) -> Dict[str, Any]:
    return {
        "type": "provided",
        "value": {"source": "ENV_VAR", "lookup": lookup},
        "confidential": True,
    }


def _always(value: Dict[str, Any]) -> Dict[str, Any]:
    return {"criteria": [{"operator": "ALWAYS_TRUE"}], "value": value}


def _write_datadir(
    base: Path, ciphertext: str, key_default_rules: List[Dict[str, Any]] | None = None
) -> str:
    (base / "configs").mkdir(parents=True)
    # Shaped like our-config quonfig.secrets.encryption.key.
    key_cfg = {
        "id": "k",
        "key": KEY_CONFIG,
        "type": "config",
        "valueType": "string",
        "default": {
            "rules": key_default_rules or [_always(_env_var_value("QUONFIG_ENCRYPTION_KEY"))]
        },
        "environments": [
            {
                "id": "production",
                "rules": [_always(_env_var_value("QUONFIG_ENCRYPTION_KEY_PROD"))],
            }
        ],
    }
    secret_cfg = {
        "id": "s",
        "key": "app.secret",
        "type": "config",
        "valueType": "string",
        "default": {
            "rules": [
                _always(
                    {
                        "type": "string",
                        "value": ciphertext,
                        "confidential": True,
                        "decryptWith": KEY_CONFIG,
                    }
                )
            ]
        },
    }
    (base / "configs" / "key.json").write_text(json.dumps(key_cfg))
    (base / "configs" / "secret.json").write_text(json.dumps(secret_cfg))
    (base / "quonfig.json").write_text(json.dumps({"environments": ["production", "staging"]}))
    return str(base)


def _client(datadir: str, env: str) -> Quonfig:
    client = Quonfig(datadir=datadir, environment=env, enable_sse=False)
    client.init()
    return client


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> Dict[str, str]:
    default_key = generate_new_hex_key()
    prod_key = generate_new_hex_key()
    monkeypatch.setenv("QUONFIG_ENCRYPTION_KEY", default_key)
    monkeypatch.setenv("QUONFIG_ENCRYPTION_KEY_PROD", prod_key)
    return {"default": default_key, "prod": prod_key}


def test_production_client_uses_env_scoped_key(tmp_path: Path, keys: Dict[str, str]) -> None:
    datadir = _write_datadir(tmp_path, encrypt("prod-secret", keys["prod"]))
    client = _client(datadir, "production")
    try:
        assert client.get_string("app.secret") == "prod-secret"
    finally:
        client.close()


def test_non_production_client_uses_default_key(tmp_path: Path, keys: Dict[str, str]) -> None:
    datadir = _write_datadir(tmp_path, encrypt("staging-secret", keys["default"]))
    client = _client(datadir, "staging")
    try:
        assert client.get_string("app.secret") == "staging-secret"
    finally:
        client.close()


def test_non_production_client_without_prod_key_set(
    tmp_path: Path, keys: Dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A staging host never has the prod key; the default rule must still serve.
    monkeypatch.delenv("QUONFIG_ENCRYPTION_KEY_PROD")
    datadir = _write_datadir(tmp_path, encrypt("staging-secret", keys["default"]))
    client = _client(datadir, "staging")
    try:
        assert client.get_string("app.secret") == "staging-secret"
    finally:
        client.close()


def test_key_config_criteria_are_honored(
    tmp_path: Path, keys: Dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    other_key = generate_new_hex_key()
    monkeypatch.setenv("OTHER_TENANT_KEY", other_key)
    rules = [
        {
            "criteria": [
                {
                    "propertyName": "tenant.name",
                    "operator": "PROP_IS_ONE_OF",
                    "valueToMatch": {"type": "string_list", "value": ["other"]},
                }
            ],
            "value": _env_var_value("OTHER_TENANT_KEY"),
        },
        _always(_env_var_value("QUONFIG_ENCRYPTION_KEY")),
    ]
    datadir = _write_datadir(
        tmp_path, encrypt("default-secret", keys["default"]), key_default_rules=rules
    )
    client = _client(datadir, "staging")
    try:
        assert client.get_string("app.secret", contexts={"tenant": {"name": "acme"}}) == (
            "default-secret"
        )
    finally:
        client.close()
