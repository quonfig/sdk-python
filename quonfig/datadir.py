from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from .types import ConfigEnvelope, ConfigResponse, Meta

logger = logging.getLogger(__name__)

SUBDIRS = ["configs", "feature-flags", "segments", "log-levels", "schemas"]


def load_datadir(datadir: str, environment: str) -> ConfigEnvelope:
    """Load all config JSON files from a workspace directory.

    Raises RuntimeError if:
    - No environment is provided (missing_environment)
    - The environment does not exist in the workspace (invalid_environment)
    """
    # Validate environment is provided
    if not environment:
        raise RuntimeError(
            "environment must be specified when using datadir mode. "
            "Set the environment option or QUONFIG_ENVIRONMENT env var."
        )

    configs: List[ConfigResponse] = []
    base = Path(datadir)

    for subdir in SUBDIRS:
        d = base / subdir
        if not d.exists():
            continue
        for path in sorted(d.glob("*.json")):
            try:
                with open(path) as f:
                    data = json.load(f)
                config = ConfigResponse.from_dict(data)
                configs.append(config)
            except Exception as e:
                logger.warning("Failed to load %s: %s", path, e)

    if not configs:
        raise RuntimeError(f"No configs loaded from {datadir}")

    # Validate that the environment exists in the loaded configs
    quonfig_json = base / "quonfig.json"
    known_environments: List[str] = []
    if quonfig_json.exists():
        try:
            with open(quonfig_json) as f:
                meta = json.load(f)
            known_environments = meta.get("environments", [])
        except Exception as e:
            logger.warning("Failed to read quonfig.json: %s", e)

    if known_environments and environment not in known_environments:
        raise RuntimeError(
            f"Environment '{environment}' not found. " f"Known environments: {known_environments}"
        )

    return ConfigEnvelope(
        configs=configs,
        meta=Meta(version="local", environment=environment),
    )
