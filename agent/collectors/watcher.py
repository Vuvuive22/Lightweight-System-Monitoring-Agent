"""Filesystem watcher using the *watchdog* library.

Monitors a set of paths for file creation, modification, deletion, and
movement.  Detected events are forwarded to a caller-supplied callback
as structured dictionaries.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

logger = logging.getLogger("sysmon-agent.watcher")


class _EventHandler(FileSystemEventHandler):
    """Internal handler that formats events and invokes the callback."""

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None],
        ignored_paths: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._callback = callback
        self._ignored_paths = ignored_paths or []

    def _is_ignored(self, path: str) -> bool:
        """Return True if the path should be ignored (e.g. self-monitoring loop)."""
        try:
            abs_path = os.path.abspath(path)
            for ignored in self._ignored_paths:
                abs_ignored = os.path.abspath(ignored)
                if abs_path == abs_ignored or abs_path.startswith(abs_ignored + os.sep):
                    return True
            # Default fallbacks to prevent infinite loop on agent's own logs
            if "sysmon-agent/agent.log" in abs_path or "sysmon-demo" in abs_path:
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # watchdog event hooks
    # ------------------------------------------------------------------

    def on_created(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileCreatedEvent):
            self._dispatch("created", event)

    def on_modified(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileModifiedEvent):
            self._dispatch("modified", event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileDeletedEvent):
            self._dispatch("deleted", event)

    def on_moved(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileMovedEvent):
            self._dispatch("moved", event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dispatch(self, event_type: str, event: FileSystemEvent) -> None:
        """Build a structured dict and pass it to the callback."""
        if self._is_ignored(event.src_path):
            return
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "src_path": event.src_path,
            "dest_path": getattr(event, "dest_path", None),
            "is_directory": event.is_directory,
            "file_size": self._safe_file_size(event.src_path),
        }
        try:
            self._callback(entry)
        except Exception:
            logger.exception(
                "Callback raised an exception for event %s on %s",
                event_type,
                event.src_path,
            )

    @staticmethod
    def _safe_file_size(path: str) -> int | None:
        """Return the file size in bytes, or ``None`` on error."""
        try:
            return os.path.getsize(path)
        except OSError:
            return None


class FileSystemWatcher:
    """Watch a list of paths and emit structured events via *callback*.

    Parameters
    ----------
    monitored_paths:
        Filesystem paths (files or directories) to observe.
    callback:
        Callable invoked with a structured event dict whenever a
        filesystem change is detected.
    recursive:
        Whether to watch sub-directories recursively.  Defaults to
        ``True``.
    """

    def __init__(
        self,
        monitored_paths: list[str],
        callback: Callable[[dict[str, Any]], None],
        *,
        recursive: bool = True,
        ignored_paths: list[str] | None = None,
    ) -> None:
        self._monitored_paths = monitored_paths
        self._callback = callback
        self._recursive = recursive
        self._ignored_paths = ignored_paths or []
        self._observer: Observer | None = None

    def start(self) -> None:
        """Start observing all configured paths.

        Paths that do not exist or are inaccessible are logged and
        skipped rather than causing the agent to abort.
        """
        if self._observer is not None:
            logger.warning("Watcher already running — ignoring start()")
            return

        self._observer = Observer()
        handler = _EventHandler(self._callback, self._ignored_paths)

        scheduled = 0
        for path in self._monitored_paths:
            if not os.path.exists(path):
                logger.warning(
                    "Monitored path does not exist, skipping: %s", path
                )
                continue
            try:
                self._observer.schedule(
                    handler, path, recursive=self._recursive
                )
                logger.info("Watching: %s (recursive=%s)", path, self._recursive)
                scheduled += 1
            except OSError as exc:
                logger.error(
                    "Cannot watch %s: %s", path, exc
                )

        if scheduled == 0:
            logger.warning("No valid paths to watch — observer not started")
            self._observer = None
            return

        self._observer.daemon = True
        self._observer.start()
        logger.info(
            "FileSystemWatcher started — observing %d path(s)", scheduled
        )

    def stop(self) -> None:
        """Stop the observer and wait for its thread to finish."""
        if self._observer is None:
            return
        logger.info("Stopping FileSystemWatcher …")
        self._observer.stop()
        self._observer.join(timeout=5)
        self._observer = None
        logger.info("FileSystemWatcher stopped")

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the observer thread is alive."""
        return self._observer is not None and self._observer.is_alive()
