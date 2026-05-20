from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

from .types import ConfigEnvelope, ConfigResponse, Meta

logger = logging.getLogger(__name__)

# schemas/ is intentionally excluded: those files are raw JSON Schema documents,
# not Configs, and SDKs do not consume them (qfg-uzsl). Matches api-delivery and
# sdk-java.
SUBDIRS = ["configs", "feature-flags", "segments", "log-levels"]


def coerce_numeric_values(data: Any) -> Any:
    """Recursively coerce int/double Value nodes from JSON strings to numbers.

    Quonfig config files store ``int`` and ``double`` value fields as JSON
    strings on disk (``{"type": "int", "value": "123"}``). api-delivery
    normalizes these at load time; this brings datadir mode to parity so the
    loaded envelope always carries real numbers, regardless of who consumes
    it (qfg-38sf.8). Mirrors sdk-go's ``Value.UnmarshalJSON``.

    A ``{type, value}`` pair uniquely identifies a Value node, so this generic
    walk safely covers default/environment rules, criteria ``valueToMatch``,
    weighted-value arms, and variants in one pass.

    On a parse failure the original string is left in place — passthrough,
    never raise — matching the cli's ``coerceNumeric`` helper.
    """
    if isinstance(data, dict):
        vtype = data.get("type")
        value = data.get("value")
        if vtype in ("int", "double") and isinstance(value, str):
            try:
                data["value"] = int(value) if vtype == "int" else float(value)
            except ValueError:
                pass  # passthrough — leave the original string
        for v in data.values():
            coerce_numeric_values(v)
    elif isinstance(data, list):
        for item in data:
            coerce_numeric_values(item)
    return data


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
                # Coerce int/double values from JSON strings to numbers BEFORE
                # building the ConfigResponse, so the loaded envelope carries
                # real numbers (qfg-38sf.8). Done on the raw dict — not in
                # Value.from_dict — because weighted-value arms are built
                # lazily at resolve time and would miss a from_dict hook.
                coerce_numeric_values(data)
                config = ConfigResponse.from_dict(data)
                # Defense-in-depth: reject empty-key configs rather than silently
                # producing a stub (qfg-uzsl). Mirrors api-delivery's loader.
                if not config.key:
                    raise ValueError("config has empty key — file is not a Quonfig Config")
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
            f"Environment '{environment}' not found. Known environments: {known_environments}"
        )

    return ConfigEnvelope(
        configs=configs,
        meta=Meta(version="local", environment=environment),
    )
