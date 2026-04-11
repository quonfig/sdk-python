from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from .types import ConfigEnvelope, ConfigResponse, Meta

logger = logging.getLogger(__name__)

SUBDIRS = ["configs", "feature-flags", "segments", "log-levels", "schemas"]


def load_datadir(datadir: str, environment: str) -> ConfigEnvelope:
    """Load all config JSON files from a workspace directory."""
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

    return ConfigEnvelope(
        configs=configs,
        meta=Meta(version="local", environment=environment),
    )
