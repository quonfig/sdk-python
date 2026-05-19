"""Filesystem watcher for opt-in datadir auto-reload (qfg-mol-3gy).

Mirrors sdk-node's `src/datadirWatcher.ts`. The Quonfig client owns the
parse-then-swap; this module only fires the debounced reload trigger.

Uses `watchfiles` (Rust-backed `notify` wrapper) — sync `watch()` iterator
runs in a daemon thread driven by a `stop_event`. `watchfiles` does its own
intra-batch debounce, so the consumer just forwards each yielded batch to
`on_change`. Symlinked datadirs are resolved at start time so edits to the
real path are detected.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

from watchfiles import watch as _watch

_LOG = logging.getLogger(__name__)


class DatadirWatcher:
    """Watch a datadir on a daemon thread and fire `on_change` per debounced burst.

    Registration failures (read-only filesystem, missing path, immutable
    container) are surfaced via `on_error` — `start()` returns ``False`` in
    that case and the watcher holds no thread. The SDK logs and continues
    serving the initial envelope.

    `close()` signals the stop event; the underlying `watchfiles.watch()`
    iterator steps every ~50ms and exits cleanly.
    """

    def __init__(
        self,
        *,
        datadir: str,
        debounce_ms: int,
        on_change: Callable[[], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        self._datadir = datadir
        self._debounce_ms = max(1, int(debounce_ms))
        self._on_change = on_change
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        # Resolve symlinks up-front so we watch the real target (matches
        # sdk-node). `os.path.realpath` does not raise on missing paths, so
        # we explicitly require the resolved target to exist — otherwise the
        # background watcher would just sit there waiting on a non-existent
        # directory.
        try:
            resolved = os.path.realpath(self._datadir)
            if not os.path.isdir(resolved):
                raise FileNotFoundError(
                    f"datadir does not exist or is not a directory: {self._datadir}"
                )
        except BaseException as err:
            self._safe_on_error(err)
            return False

        self._thread = threading.Thread(
            target=self._run,
            args=(resolved,),
            daemon=True,
            name="quonfig-datadir-watcher",
        )
        self._thread.start()
        return True

    def _run(self, resolved: str) -> None:
        try:
            # `step` controls how often the watcher checks `stop_event`; keep
            # it short so close() exits the thread quickly. `debounce` is the
            # quiet-period that triggers a yield, which is exactly the
            # coalesce-burst behavior we want.
            for _changes in _watch(
                resolved,
                stop_event=self._stop_event,
                step=50,
                debounce=self._debounce_ms,
                rust_timeout=0,
                yield_on_timeout=False,
                raise_interrupt=False,
                recursive=True,
            ):
                if self._stop_event.is_set():
                    return
                try:
                    self._on_change()
                except Exception as e:  # noqa: BLE001 — callback errors must not kill the watcher
                    _LOG.warning("datadir auto-reload callback raised: %s: %s", type(e).__name__, e)
        except BaseException as e:  # noqa: BLE001 — surface and exit
            if not self._stop_event.is_set():
                self._safe_on_error(e)

    def _safe_on_error(self, err: BaseException) -> None:
        try:
            self._on_error(err)
        except Exception:  # noqa: BLE001
            _LOG.exception("on_error callback raised; suppressing")

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            # `watchfiles.watch` checks the stop event every `step` ms (50);
            # give it a few cycles before giving up. We hold a daemon thread,
            # so a stuck join won't block process exit.
            thread.join(timeout=2.0)
        self._thread = None
