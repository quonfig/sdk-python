from __future__ import annotations

import contextvars
import time
from typing import Any, Optional, Tuple

from .types import Contexts

# The scoped context lives in a ContextVar, not threading.local(): under
# asyncio many tasks share one thread, so a thread-local let interleaved
# request scopes evaluate each other's context and leak one onto the thread
# (qfg-goi1.2.13). A ContextVar is per thread AND per asyncio task (a task
# copies the context it was created in), so sync threaded code behaves exactly
# as before.
_scoped_context: contextvars.ContextVar[Optional[Contexts]] = contextvars.ContextVar(
    "quonfig_scoped_context", default=None
)

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
    """Set the scoped context for the current thread / asyncio task."""
    _scoped_context.set(contexts)


def get_thread_context() -> Optional[Contexts]:
    """Return the scoped context of the current thread / asyncio task."""
    return _scoped_context.get()


def clear_thread_context() -> None:
    """Clear the scoped context of the current thread / asyncio task."""
    _scoped_context.set(None)


def push_scoped_context(contexts: Contexts) -> "contextvars.Token[Optional[Contexts]]":
    """Install ``contexts`` as the scoped context and return the token that
    :func:`pop_scoped_context` uses to restore exactly the previous value."""
    return _scoped_context.set(contexts)


def pop_scoped_context(token: "contextvars.Token[Optional[Contexts]]") -> None:
    """Restore the scoped context that was current before the matching push."""
    _scoped_context.reset(token)
