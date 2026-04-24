"""Regression tests for telemetry wiring in the Quonfig client.

Covers two bugs previously confirmed by live-fire:

1. The default telemetry URL must be ``https://telemetry.quonfig.com`` so
   events don't silently POST to ``api.quonfig.com`` and 404. This matches
   sdk-node / sdk-go.

2. Datadir-mode init must call ``TelemetryReporter.start()``. Previously
   only api-mode init started the reporter, so any datadir-backed service
   (e.g. api-delivery, api-telemetry) emitted zero telemetry.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quonfig import Quonfig


def _make_minimal_datadir(tmp_path: Path) -> str:
    """Create a workspace with one trivial config so load_datadir succeeds."""
    datadir = tmp_path / "workspace"
    datadir.mkdir()
    (datadir / "quonfig.json").write_text(
        json.dumps({"environments": ["Production"]}), encoding="utf-8"
    )
    configs_dir = datadir / "configs"
    configs_dir.mkdir()
    (configs_dir / "noop.json").write_text(
        json.dumps(
            {
                "id": "noop-id",
                "key": "noop",
                "type": "config",
                "valueType": "string",
                "sendToClientSdk": False,
                "default": {
                    "rules": [
                        {
                            "criteria": [{"operator": "ALWAYS_TRUE"}],
                            "value": {"type": "string", "value": "ok"},
                        }
                    ]
                },
                "environments": [
                    {
                        "id": "Production",
                        "rules": [
                            {
                                "criteria": [{"operator": "ALWAYS_TRUE"}],
                                "value": {"type": "string", "value": "ok"},
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(datadir)


def test_default_telemetry_url_points_at_telemetry_host(monkeypatch: Any) -> None:
    # No env overrides — construction must default to telemetry.quonfig.com,
    # not api.quonfig.com. See sdk-node DEFAULT_TELEMETRY_URL.
    monkeypatch.delenv("QUONFIG_TELEMETRY_URL", raising=False)
    client = Quonfig(sdk_key="qf_sk_development_0000_dead")
    assert client._telemetry_url == "https://telemetry.quonfig.com"


def test_env_override_still_wins(monkeypatch: Any) -> None:
    monkeypatch.setenv("QUONFIG_TELEMETRY_URL", "https://custom.example/")
    client = Quonfig(sdk_key="qf_sk_development_0000_dead")
    assert client._telemetry_url == "https://custom.example/"


def test_datadir_mode_starts_telemetry_reporter(tmp_path: Path) -> None:
    datadir = _make_minimal_datadir(tmp_path)
    client = Quonfig(
        sdk_key="qf_sk_development_0000_dead",
        datadir=datadir,
        environment="Production",
    )
    client.init()

    # The reporter thread must exist and be alive after init — previously
    # _load_from_datadir never called start() and the thread was None.
    assert client._telemetry is not None, "telemetry should be constructed by default"
    thread = client._telemetry._thread
    assert thread is not None, "datadir init must call TelemetryReporter.start()"
    assert thread.is_alive(), "telemetry thread should be running after init"

    client.close()
