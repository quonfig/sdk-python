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
        # Canonical-ordering watermark (qfg-7h5d.1.8). ``_generation`` is the
        # Meta.generation of the currently-installed envelope; ``_installs``
        # counts every accepted install across the client's lifetime (initial
        # fetch, failover/poll fetch, SSE snapshot/update). The reject-older
        # guard reads both: a fresh store (``_installs == 0``) accepts anything,
        # an established store accepts only a strictly-higher generation.
        self._generation: int = 0
        self._installs: int = 0

    def get(self, key: str) -> Optional[ConfigResponse]:
        with self._lock:
            return self._configs.get(key)

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._configs.keys())

    def update(self, envelope: ConfigEnvelope, *, guard: bool = False) -> bool:
        """Atomically replace all configs from a ConfigEnvelope.

        Returns ``True`` when the envelope was installed, ``False`` when the
        reject-older guard dropped it.

        When ``guard`` is ``True`` (every network install path: initial fetch,
        failover/poll fetch, SSE snapshot/update) the canonical-ordering rule
        applies — an established store installs only if the incoming
        ``Meta.generation`` is strictly greater than the held generation, so a
        late failover to a stale secondary can never move the client backward
        and an equal second leg is a no-op. A fresh store (nothing installed)
        always seeds off the first snapshot it sees, even at generation 0.

        When ``guard`` is ``False`` (the default — datadir load/reload) the
        install is unconditional: a local data dir is the source of truth and
        always reports generation 0, so it must not be gated by the watermark.
        """
        with self._lock:
            if guard and self._installs > 0 and envelope.meta.generation <= self._generation:
                return False
            self._configs = {c.key: c for c in envelope.configs}
            self._etag = envelope.meta.version
            # In SDK-key delivery mode the server scopes each config to a single
            # active environment and reports its id here. The evaluator uses this
            # as the env id when the consumer did NOT pin one (qfg-xpln.3).
            self._meta_environment = envelope.meta.environment
            self._generation = envelope.meta.generation
            self._installs += 1
            return True

    def get_etag(self) -> Optional[str]:
        with self._lock:
            return self._etag

    def get_generation(self) -> int:
        """Meta.generation of the currently-installed envelope (0 before the
        first install or in datadir mode)."""
        with self._lock:
            return self._generation

    def install_count(self) -> int:
        """Number of envelopes installed over the store's lifetime. The
        reject-older guard keeps this from advancing on a same-or-older
        payload."""
        with self._lock:
            return self._installs

    def get_meta_environment(self) -> str:
        """The active environment id reported by the server (``meta.environment``)."""
        with self._lock:
            return self._meta_environment
