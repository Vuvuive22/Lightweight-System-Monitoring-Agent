"""System metrics collector using psutil.

Collects CPU, RAM, disk, and network statistics and returns them as
structured dictionaries ready for logging or serialisation.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import psutil

logger = logging.getLogger("sysmon-agent.metrics")


class SystemMetricsCollector:
    """Gather point-in-time system metrics.

    Network counters are tracked across calls so that both **delta**
    (throughput per second) and **cumulative** values are reported.
    """

    def __init__(self, disk_mount_points: list[str] | None = None) -> None:
        self._disk_mount_points: list[str] = disk_mount_points or ["/"]

        # Seed CPU baseline for non-blocking collection.
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        # Seed network baseline for delta calculations.
        try:
            counters = psutil.net_io_counters()
            self._prev_net_bytes_sent: int = counters.bytes_sent
            self._prev_net_bytes_recv: int = counters.bytes_recv
        except (OSError, AttributeError):
            self._prev_net_bytes_sent = 0
            self._prev_net_bytes_recv = 0
        self._prev_net_time: float = time.monotonic()

    # ------------------------------------------------------------------
    # Individual collectors
    # ------------------------------------------------------------------

    def collect_cpu(self) -> dict[str, Any] | None:
        """Return overall CPU usage percentage using a non-blocking baseline."""
        try:
            percent = psutil.cpu_percent(interval=None)
            return {
                "cpu_percent": percent,
                "cpu_count_logical": psutil.cpu_count(logical=True),
                "cpu_count_physical": psutil.cpu_count(logical=False),
            }
        except (OSError, psutil.Error) as exc:
            logger.error("Failed to collect CPU metrics: %s", exc)
            return None

    def collect_memory(self) -> dict[str, Any] | None:
        """Return RAM usage details."""
        try:
            mem = psutil.virtual_memory()
            return {
                "total_bytes": mem.total,
                "available_bytes": mem.available,
                "used_bytes": mem.used,
                "used_percent": mem.percent,
            }
        except (OSError, psutil.Error) as exc:
            logger.error("Failed to collect memory metrics: %s", exc)
            return None

    def collect_disk(self) -> dict[str, dict[str, Any]] | None:
        """Return disk usage for each configured mount point."""
        results: dict[str, dict[str, Any]] = {}
        for mount in self._disk_mount_points:
            try:
                usage = psutil.disk_usage(mount)
                results[mount] = {
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "used_percent": usage.percent,
                }
            except (OSError, psutil.Error) as exc:
                logger.error(
                    "Failed to collect disk metrics for %s: %s", mount, exc
                )
                results[mount] = None  # type: ignore[assignment]
        return results if results else None

    def collect_network(self) -> dict[str, Any] | None:
        """Return both cumulative and delta network I/O statistics.

        Delta values are normalised to *bytes per second* based on the
        elapsed wall-clock time since the previous call.
        """
        try:
            counters = psutil.net_io_counters()
            now = time.monotonic()
            elapsed = max(now - self._prev_net_time, 0.001)  # avoid div/0

            delta_sent = counters.bytes_sent - self._prev_net_bytes_sent
            delta_recv = counters.bytes_recv - self._prev_net_bytes_recv

            result: dict[str, Any] = {
                "cumulative": {
                    "bytes_sent": counters.bytes_sent,
                    "bytes_recv": counters.bytes_recv,
                    "packets_sent": counters.packets_sent,
                    "packets_recv": counters.packets_recv,
                    "errin": counters.errin,
                    "errout": counters.errout,
                    "dropin": counters.dropin,
                    "dropout": counters.dropout,
                },
                "delta": {
                    "bytes_sent": delta_sent,
                    "bytes_recv": delta_recv,
                    "bytes_sent_per_sec": round(delta_sent / elapsed, 2),
                    "bytes_recv_per_sec": round(delta_recv / elapsed, 2),
                    "elapsed_seconds": round(elapsed, 3),
                },
            }

            # Update baseline for next call.
            self._prev_net_bytes_sent = counters.bytes_sent
            self._prev_net_bytes_recv = counters.bytes_recv
            self._prev_net_time = now

            return result
        except (OSError, AttributeError, psutil.Error) as exc:
            logger.error("Failed to collect network metrics: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def collect_all(self) -> dict[str, Any]:
        """Run every collector and return a single timestamped snapshot."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu": self.collect_cpu(),
            "memory": self.collect_memory(),
            "disk": self.collect_disk(),
            "network": self.collect_network(),
        }
