"""Fork safety for the Quonfig Python SDK (qfg-lv4n.2).

After ``os.fork()`` the child inherits copies of the parent's SSE, poll,
telemetry and datadir-watcher threads that do not exist any more: the config
goes permanently stale while ``connection_state()`` keeps answering from the
inherited state. That is the Cheddar Up incident (epic qfg-lv4n) — a worker
served a 13-day-old snapshot while the SDK reported itself healthy. It reaches
Python through Gunicorn ``--preload``, Celery prefork, uWSGI and
``multiprocessing`` with the ``fork`` start method.

The design is Reforge sdk-python 1.2.2's, unchanged in principle
(``sdk_reforge/__init__.py``, "Fix fork safety for singleton SDK" #26):

* **Child only.** One ``os.register_at_fork(after_in_child=...)`` handler,
  registered once at import and guarded by ``hasattr`` (Windows, and the
  ``spawn`` / ``forkserver`` start methods, get a no-op — a spawned child runs
  a fresh interpreter and constructs its own client). There is deliberately no
  ``before=`` and no ``after_in_parent=`` handler: **the parent is never
  touched.** Tearing the parent down before the syscall is what broke sdk-ruby
  (qfg-ryov), and no SDK in the surveyed corpus — LaunchDarkly, Statsig,
  dd-trace-rb, sentry-ruby, redis-client, connection_pool, Reforge — does it.
* **Drop inherited state, including locks, then rebuild fresh.** A
  ``threading.Lock`` held by a thread that did not survive the fork stays
  locked forever in the child, so Reforge swaps its module ``_ReadWriteLock``
  for a fresh one rather than trying to use it. Same here, per instance.

The one forced difference from Reforge: Quonfig has no module-level singleton
to reset — customers construct and hold ``quonfig.Quonfig`` instances — so
live instances are tracked in a ``weakref.WeakSet`` and rebuilt in place. See
"Reforge parity: known, accepted differences" in qfg-lv4n.2.
"""

from __future__ import annotations

import logging
import os
import threading
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle guard only
    from .client import Quonfig

logger = logging.getLogger(__name__)

# Live clients, weakly held: a client that goes out of scope is collected
# normally and never leaks through this registry. Mirrors sdk-ruby's
# ``ObjectSpace::WeakMap`` registry (lib/quonfig/client.rb). Clients that were
# closed stay in the set until they are collected; the child-side hook
# early-returns on them, so they are a no-op.
_instances: "weakref.WeakSet[Quonfig]" = weakref.WeakSet()

# Guards registration only. Replaced (never acquired) in the child — see
# ``_after_fork_in_child``.
_registry_lock = threading.Lock()


def register_instance(client: "Quonfig") -> None:
    """Track a fully-constructed client so the at-fork hook can find it."""
    with _registry_lock:
        _instances.add(client)


def _after_fork_in_child() -> None:
    """Rebuild every live client. Runs ONLY in a forked child."""
    global _registry_lock

    # The registry lock may have been held by a thread that did not survive
    # the fork, in which case acquiring it here would hang the child forever.
    # Replace it before reading the set — the same move Reforge makes with its
    # module lock.
    _registry_lock = threading.Lock()

    try:
        clients = list(_instances)
    except Exception:  # noqa: BLE001 - a fork handler must never raise
        logger.exception("[quonfig] could not enumerate SDK clients after fork")
        return

    for client in clients:
        try:
            client._rebuild_after_fork_in_child()
        except Exception:  # noqa: BLE001 - one bad client must not strand the rest
            logger.exception("[quonfig] rebuilding an SDK client after fork failed")


# Registered once, at import. ``quonfig/__init__.py`` imports this module, so
# importing the package is enough — customers wire nothing.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)
