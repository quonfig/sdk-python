"""Logging integrations for the Quonfig Python SDK.

Provides a stdlib ``logging.Filter`` subclass and a ``structlog`` processor
that dynamically gate log records based on a Quonfig-driven log-level config.

Both adapters delegate to ``Quonfig.should_log(logger_path=..., desired_level=...)``,
so they share the same per-logger routing (context injection keyed off
``quonfig-sdk-logging.key``) and the same "unknown level / missing config ->
emit" defaults as the SDK primitive.

``QuonfigLoggerFilter`` is always available — ``logging`` is part of the stdlib.
``QuonfigLoggerProcessor`` is only a real processor when ``structlog`` is
importable; otherwise instantiating it raises ``ImportError`` with a clear
install hint.

Usage (stdlib logging)::

    import logging
    from quonfig import Quonfig, QuonfigLoggerFilter

    client = Quonfig(sdk_key="sdk-...", logger_key="log-level.my-app").init()

    root = logging.getLogger()
    root.addFilter(QuonfigLoggerFilter(client))

Usage (structlog)::

    import structlog
    from quonfig import Quonfig, QuonfigLoggerProcessor

    client = Quonfig(sdk_key="sdk-...", logger_key="log-level.my-app").init()

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            QuonfigLoggerProcessor(client),
            structlog.processors.JSONRenderer(),
        ],
    )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

try:  # structlog is optional
    from structlog import DropEvent as _StructlogDropEvent

    _STRUCTLOG_AVAILABLE = True
except ImportError:  # pragma: no cover - import guard
    _STRUCTLOG_AVAILABLE = False
    _StructlogDropEvent = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from .client import Quonfig


# logging.getLevelName returns the string "Level N" for unknown numbers, but
# for known levels it returns the string; we need the reverse for the filter,
# which already has record.levelname (a string like "DEBUG"). structlog gives
# us a method_name / level_number that we normalize below.

# Map structlog method/level aliases to stdlib level names.
_STRUCTLOG_LEVEL_ALIASES = {
    "warn": "WARNING",
    "exception": "ERROR",
}


def _level_name_for_structlog(
    method_name: str, event_dict: dict
) -> Optional[str]:
    """Derive a level name string suitable for Quonfig.should_log from a
    structlog event.

    Priority:
      1. ``level_number`` in the event_dict (set by some processors) — convert
         to a level name via ``logging.getLevelName`` if it maps.
      2. ``level`` in the event_dict (string) — structlog.stdlib.add_log_level.
      3. the ``method_name`` the user called (``info``, ``warn``, etc.).

    Returns ``None`` if no usable level can be extracted — the caller should
    then skip filtering and let the record through.
    """
    numeric = event_dict.get("level_number")
    if isinstance(numeric, int):
        name = logging.getLevelName(numeric)
        if isinstance(name, str) and not name.startswith("Level "):
            return name

    raw = event_dict.get("level") or method_name
    if not raw:
        return None

    raw = str(raw).lower()
    mapped = _STRUCTLOG_LEVEL_ALIASES.get(raw, raw.upper())

    # Ensure it's a level stdlib knows about. ``getLevelName`` returns an int
    # for known names and a "Level N" string for unknown ones.
    numeric_lookup = logging.getLevelName(mapped)
    if isinstance(numeric_lookup, int):
        return mapped
    return None


class QuonfigLoggerFilter(logging.Filter):
    """stdlib ``logging.Filter`` that gates records via Quonfig config.

    Each record's ``record.name`` is used as the logger path by default —
    override :meth:`logger_name` for custom routing (e.g. stripping a prefix
    or reading a structured attribute).

    If the client has no ``logger_key`` configured, or evaluation raises, the
    record is allowed through — the filter is best-effort and never masks logs
    due to its own misconfiguration.
    """

    def __init__(
        self,
        quonfig: Optional["Quonfig"] = None,
        *,
        logger_path: Optional[str] = None,
    ) -> None:
        """
        Args:
            quonfig: Initialized ``Quonfig`` client. ``None`` disables filtering
                (all records pass) — useful for tests or deferred wiring.
            logger_path: Optional fixed logger path to use for every record,
                instead of ``record.name``. Rarely needed, but handy when the
                filter is attached to a handler that fans in multiple loggers
                and you want them all gated together.
        """
        super().__init__()
        self._quonfig = quonfig
        self._logger_path = logger_path

    def logger_name(self, record: logging.LogRecord) -> str:
        """Return the logger path string used for config lookup.

        Override to derive a different path from the record (e.g. using
        ``record.module`` or a custom attribute).
        """
        if self._logger_path is not None:
            return self._logger_path
        return record.name

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if self._quonfig is None:
            return True

        path = self.logger_name(record)
        if not path:
            return True

        try:
            return self._quonfig.should_log(
                logger_path=path,
                desired_level=record.levelname,
            )
        except Exception:
            # Best-effort: never mask logs due to SDK-side errors.
            return True


class QuonfigLoggerProcessor:
    """structlog processor that gates events via Quonfig config.

    Expects ``structlog.stdlib.add_log_level`` (or equivalent) to be earlier
    in the processor chain so that ``event_dict["level"]`` is populated. Falls
    back to the structlog method name (``info``, ``warn``, etc.) if the event
    dict has no level.

    The callable signature matches the structlog processor contract:
    ``(logger, method_name, event_dict) -> event_dict`` (or raises
    ``structlog.DropEvent``).
    """

    def __init__(
        self,
        quonfig: Optional["Quonfig"] = None,
        *,
        logger_path: Optional[str] = None,
    ) -> None:
        """
        Args:
            quonfig: Initialized ``Quonfig`` client. ``None`` disables filtering.
            logger_path: Optional fixed logger path. Overrides the default
                extraction (``logger.name`` / ``event_dict["logger"]``).

        Raises:
            ImportError: if ``structlog`` is not installed.
        """
        if not _STRUCTLOG_AVAILABLE:
            raise ImportError(
                "QuonfigLoggerProcessor requires the optional 'structlog' dependency. "
                "Install it with: pip install structlog"
            )
        self._quonfig = quonfig
        self._logger_path = logger_path

    def logger_name(self, logger: Any, event_dict: dict) -> Optional[str]:
        """Return the logger path for this event.

        Default resolution order:
          1. the ``logger_path`` constructor arg (if set)
          2. ``logger.name`` if present
          3. ``event_dict["logger"]`` (structlog's conventional key)
        """
        if self._logger_path is not None:
            return self._logger_path
        return getattr(logger, "name", None) or event_dict.get("logger")

    def __call__(
        self, logger: Any, method_name: str, event_dict: dict
    ) -> dict:
        if self._quonfig is None:
            return event_dict

        level_name = _level_name_for_structlog(method_name, event_dict)
        if level_name is None:
            return event_dict

        path = self.logger_name(logger, event_dict)
        if not path:
            return event_dict

        try:
            allowed = self._quonfig.should_log(
                logger_path=path,
                desired_level=level_name,
            )
        except Exception:
            return event_dict

        if not allowed:
            assert _StructlogDropEvent is not None  # structlog present
            raise _StructlogDropEvent
        return event_dict


__all__ = [
    "QuonfigLoggerFilter",
    "QuonfigLoggerProcessor",
]
