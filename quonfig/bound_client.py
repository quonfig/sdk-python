from __future__ import annotations

from typing import Any, List, Optional

from .client import _NO_DEFAULT, Quonfig
from .context import merge_contexts
from .types import Contexts


class BoundQuonfig:
    """
    A Quonfig client bound to a specific context.

    All getters automatically merge the bound contexts with any additional
    per-call contexts.
    """

    def __init__(self, client: Quonfig, contexts: Contexts) -> None:
        self._client = client
        self._contexts = contexts

    def with_context(self, contexts: Contexts) -> "BoundQuonfig":
        """Return a new BoundQuonfig with additional contexts merged in."""
        return BoundQuonfig(self._client, merge_contexts(self._contexts, contexts))

    def get(self, key: str, default: Any = _NO_DEFAULT) -> Any:
        return self._client.get(key, default=default, contexts=self._contexts)

    def get_string(self, key: str, default: Any = _NO_DEFAULT) -> Optional[str]:
        return self._client.get_string(key, default=default, contexts=self._contexts)

    def get_int(self, key: str, default: Any = _NO_DEFAULT) -> Optional[int]:
        return self._client.get_int(key, default=default, contexts=self._contexts)

    def get_float(self, key: str, default: Any = _NO_DEFAULT) -> Optional[float]:
        return self._client.get_float(key, default=default, contexts=self._contexts)

    def get_bool(self, key: str, default: Any = _NO_DEFAULT) -> Optional[bool]:
        return self._client.get_bool(key, default=default, contexts=self._contexts)

    def get_string_list(
        self, key: str, default: Any = _NO_DEFAULT
    ) -> Optional[List[str]]:
        return self._client.get_string_list(
            key, default=default, contexts=self._contexts
        )

    def get_json(self, key: str, default: Any = _NO_DEFAULT) -> Any:
        return self._client.get_json(key, default=default, contexts=self._contexts)

    def get_duration(self, key: str, default: Any = _NO_DEFAULT) -> Optional[float]:
        return self._client.get_duration(
            key, default=default, contexts=self._contexts
        )

    def is_feature_enabled(self, key: str, default: bool = False) -> bool:
        return self._client.is_feature_enabled(
            key, default=default, contexts=self._contexts
        )

    def should_log(self, logger_name: str, desired_level: str) -> bool:
        return self._client.should_log(
            logger_name, desired_level, contexts=self._contexts
        )

    def keys(self) -> List[str]:
        return self._client.keys()
