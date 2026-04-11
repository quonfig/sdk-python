from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .types import ConfigEnvelope, ConfigResponse


class ConfigStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._configs: Dict[str, ConfigResponse] = {}
        self._etag: Optional[str] = None

    def get(self, key: str) -> Optional[ConfigResponse]:
        with self._lock:
            return self._configs.get(key)

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._configs.keys())

    def update(self, envelope: ConfigEnvelope) -> None:
        """Atomically replace all configs from a ConfigEnvelope."""
        with self._lock:
            self._configs = {c.key: c for c in envelope.configs}
            self._etag = envelope.meta.version

    def get_etag(self) -> Optional[str]:
        with self._lock:
            return self._etag
