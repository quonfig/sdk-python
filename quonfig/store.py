from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .types import ConfigEnvelope, ConfigResponse


class ConfigStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._configs: Dict[str, ConfigResponse] = {}
        self._etag: Optional[str] = None
        self._meta_environment: str = ""

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
            # In SDK-key delivery mode the server scopes each config to a single
            # active environment and reports its id here. The evaluator uses this
            # as the env id when the consumer did NOT pin one (qfg-xpln.3).
            self._meta_environment = envelope.meta.environment

    def get_etag(self) -> Optional[str]:
        with self._lock:
            return self._etag

    def get_meta_environment(self) -> str:
        """The active environment id reported by the server (``meta.environment``)."""
        with self._lock:
            return self._meta_environment
