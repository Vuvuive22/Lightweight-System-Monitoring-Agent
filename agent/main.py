#!/usr/bin/env python3
"""sysmon-agent — Lightweight System Monitoring Agent.

Main daemon entry point.  Orchestrates the metrics collector, filesystem
watcher, and logger, running them as a long-lived background service.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from agent.collectors.metrics import SystemMetricsCollector
from agent.collectors.watcher import FileSystemWatcher
from agent.utils.config import load_config
from agent.utils.logger import setup_logger
from agent.utils.dashboard import DashboardServer

# Default configuration search paths (in priority order).
_CONFIG_PATHS: list[str] = [
    "/etc/sysmon-agent/config.json",
    "./config/config.json",
]

logger: logging.Logger = logging.getLogger("sysmon-agent.main")


class SysmonDaemon:
    """Top-level daemon that ties all sub-systems together."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._stop_event = threading.Event()

        # --- Sub-systems ---
        self._collector = SystemMetricsCollector(
            disk_mount_points=config.get("disk_mount_points", ["/"]),
        )
        log_path = config.get("logging", {}).get("log_file_path", "")
        self._watcher = FileSystemWatcher(
            monitored_paths=config.get("monitored_paths", []),
            callback=self._on_fs_event,
            ignored_paths=[log_path] if log_path else None,
        )
        self._dashboard = DashboardServer(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start all sub-systems and block until a stop signal arrives."""
        logger.info("Starting sysmon-agent daemon …")

        # Start filesystem watcher (runs in its own daemon thread).
        self._watcher.start()

        # Start dashboard HTTP server (runs in its own background thread).
        self._dashboard.start()

        # Enter the periodic metrics collection loop.
        interval: float = float(self._config.get("interval", 30))
        logger.info("Metrics collection interval: %.1fs", interval)

        try:
            while not self._stop_event.is_set():
                self._collect_and_log_metrics()
                # Wait for the configured interval (or until stop_event).
                self._stop_event.wait(timeout=interval)
        except Exception:
            logger.exception("Unhandled exception in main loop")
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal the main loop to exit gracefully."""
        logger.info("Stop signal received")
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_and_log_metrics(self) -> None:
        """Run a single metrics collection cycle and log the result."""
        try:
            snapshot = self._collector.collect_all()
            logger.info("metrics_snapshot %s", json.dumps(snapshot))
        except Exception:
            logger.exception("Error during metrics collection")

    def _on_fs_event(self, event: dict[str, Any]) -> None:
        """Callback invoked by the filesystem watcher for each event."""
        logger.info("fs_event %s", json.dumps(event))

    def _shutdown(self) -> None:
        """Cleanly shut down all sub-systems."""
        logger.info("Shutting down sysmon-agent …")
        self._watcher.stop()
        self._dashboard.stop()
        # Flush and close all log handlers.
        for handler in logging.getLogger("sysmon-agent").handlers[:]:
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
        logger.info("sysmon-agent stopped")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def _find_config_path() -> str:
    """Return the first existing config file path, or fall back."""
    # Allow override via command-line argument.
    if len(sys.argv) > 1:
        return sys.argv[1]
    for path in _CONFIG_PATHS:
        if Path(path).is_file():
            return path
    # Fall back to the repo-local config for development.
    return _CONFIG_PATHS[-1]


def main() -> None:
    """Resolve configuration, wire up signals, and run the daemon."""
    config_path = _find_config_path()
    config = load_config(config_path)
    setup_logger(config)

    daemon = SysmonDaemon(config)

    # Register signal handlers for graceful shutdown.
    def _handle_signal(signum: int, _frame: Any) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — initiating shutdown", sig_name)
        daemon.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    daemon.run()


if __name__ == "__main__":
    main()
