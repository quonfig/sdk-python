from __future__ import annotations

import threading
import time
from typing import Any, Optional, Tuple

from .types import Contexts

_thread_local = threading.local()

# Magic property names that resolve to current time in milliseconds
_MAGIC_TIME_PROPS = frozenset(
    ["prefab.current-time", "quonfig.current-time", "reforge.current-time"]
)


def merge_contexts(*contexts_list: Contexts) -> Contexts:
    """Shallow merge per namespace; later wins."""
    result: Contexts = {}
    for ctx in contexts_list:
        if not ctx:
            continue
        for namespace, values in ctx.items():
            result[namespace] = dict(values)
    return result


def get_context_value(contexts: Contexts, property_name: str) -> Tuple[Any, bool]:
    """
    Dotted-path lookup: "user.email" -> contexts["user"]["email"].

    Magic properties are resolved before normal lookup:
      - "prefab.current-time", "quonfig.current-time", "reforge.current-time"
        -> current Unix time in ms

    Returns (value, found: bool).
    """
    if property_name in _MAGIC_TIME_PROPS:
        return int(time.time() * 1000), True

    if not property_name:
        return None, False

    parts = property_name.split(".", maxsplit=1)
    if len(parts) == 1:
        # No namespace — look in "" namespace
        namespace = ""
        key = property_name
    else:
        namespace, key = parts

    ns_data = contexts.get(namespace)
    if ns_data is None:
        return None, False

    if key in ns_data:
        return ns_data[key], True
    return None, False


def set_thread_context(contexts: Contexts) -> None:
    """Store contexts in thread-local storage."""
    _thread_local.quonfig_context = contexts


def get_thread_context() -> Optional[Contexts]:
    """Retrieve contexts from thread-local storage."""
    return getattr(_thread_local, "quonfig_context", None)


def clear_thread_context() -> None:
    """Remove contexts from thread-local storage."""
    if hasattr(_thread_local, "quonfig_context"):
        del _thread_local.quonfig_context
