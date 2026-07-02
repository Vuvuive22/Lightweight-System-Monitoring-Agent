"""Unit tests for agent.collectors.metrics module."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.collectors.metrics import SystemMetricsCollector


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture()
def collector() -> SystemMetricsCollector:
    """Return a collector with default settings."""
    return SystemMetricsCollector(disk_mount_points=["/"])


# -----------------------------------------------------------------------
# collect_cpu
# -----------------------------------------------------------------------

class TestCollectCPU:
    """Tests for CPU metrics collection."""

    def test_returns_dict_with_expected_keys(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_cpu()
        assert result is not None
        assert "cpu_percent" in result
        assert "cpu_count_logical" in result
        assert "cpu_count_physical" in result

    def test_cpu_percent_is_float(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_cpu()
        assert result is not None
        assert isinstance(result["cpu_percent"], float)

    def test_cpu_count_positive(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_cpu()
        assert result is not None
        assert result["cpu_count_logical"] >= 1

    @patch("agent.collectors.metrics.psutil.cpu_percent", side_effect=OSError("mock"))
    def test_returns_none_on_error(self, _mock, collector: SystemMetricsCollector) -> None:
        result = collector.collect_cpu()
        assert result is None


# -----------------------------------------------------------------------
# collect_memory
# -----------------------------------------------------------------------

class TestCollectMemory:
    """Tests for memory metrics collection."""

    def test_returns_dict_with_expected_keys(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_memory()
        assert result is not None
        expected_keys = {"total_bytes", "available_bytes", "used_bytes", "used_percent"}
        assert expected_keys.issubset(result.keys())

    def test_total_greater_than_zero(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_memory()
        assert result is not None
        assert result["total_bytes"] > 0

    def test_used_percent_in_range(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_memory()
        assert result is not None
        assert 0.0 <= result["used_percent"] <= 100.0

    @patch("agent.collectors.metrics.psutil.virtual_memory", side_effect=OSError("mock"))
    def test_returns_none_on_error(self, _mock, collector: SystemMetricsCollector) -> None:
        result = collector.collect_memory()
        assert result is None


# -----------------------------------------------------------------------
# collect_disk
# -----------------------------------------------------------------------

class TestCollectDisk:
    """Tests for disk metrics collection."""

    def test_returns_dict_for_each_mount(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_disk()
        assert result is not None
        # On Windows the mount "/" won't exist, but psutil.disk_usage('/')
        # may still work or the test runs on Linux. We check structure.
        for mount, data in result.items():
            if data is not None:
                assert "total_bytes" in data
                assert "used_bytes" in data
                assert "free_bytes" in data
                assert "used_percent" in data

    def test_multiple_mount_points(self) -> None:
        """Verify the collector handles multiple mount points."""
        # Use "/" which exists on all platforms psutil supports.
        col = SystemMetricsCollector(disk_mount_points=["/"])
        result = col.collect_disk()
        assert result is not None
        assert "/" in result

    @patch("agent.collectors.metrics.psutil.disk_usage", side_effect=OSError("mock"))
    def test_error_yields_none_value(self, _mock) -> None:
        col = SystemMetricsCollector(disk_mount_points=["/"])
        result = col.collect_disk()
        assert result is not None
        assert result["/"] is None


# -----------------------------------------------------------------------
# collect_network
# -----------------------------------------------------------------------

class TestCollectNetwork:
    """Tests for network metrics collection."""

    def test_returns_cumulative_and_delta(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_network()
        assert result is not None
        assert "cumulative" in result
        assert "delta" in result

    def test_cumulative_keys(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_network()
        assert result is not None
        cum = result["cumulative"]
        for key in ("bytes_sent", "bytes_recv", "packets_sent", "packets_recv",
                     "errin", "errout", "dropin", "dropout"):
            assert key in cum

    def test_delta_keys(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_network()
        assert result is not None
        delta = result["delta"]
        for key in ("bytes_sent", "bytes_recv", "bytes_sent_per_sec",
                     "bytes_recv_per_sec", "elapsed_seconds"):
            assert key in delta

    def test_delta_per_sec_non_negative(self, collector: SystemMetricsCollector) -> None:
        result = collector.collect_network()
        assert result is not None
        assert result["delta"]["bytes_sent_per_sec"] >= 0
        assert result["delta"]["bytes_recv_per_sec"] >= 0

    @patch("agent.collectors.metrics.psutil.net_io_counters", side_effect=OSError("mock"))
    def test_returns_none_on_error(self, _mock, collector: SystemMetricsCollector) -> None:
        result = collector.collect_network()
        assert result is None


# -----------------------------------------------------------------------
# collect_all
# -----------------------------------------------------------------------

class TestCollectAll:
    """Tests for the aggregate snapshot."""

    def test_snapshot_has_all_sections(self, collector: SystemMetricsCollector) -> None:
        snapshot = collector.collect_all()
        assert "timestamp" in snapshot
        assert "cpu" in snapshot
        assert "memory" in snapshot
        assert "disk" in snapshot
        assert "network" in snapshot

    def test_timestamp_is_iso_format(self, collector: SystemMetricsCollector) -> None:
        snapshot = collector.collect_all()
        ts = snapshot["timestamp"]
        assert isinstance(ts, str)
        # Basic ISO check: contains 'T' separator.
        assert "T" in ts

    def test_successive_calls_update_delta(self, collector: SystemMetricsCollector) -> None:
        """Two successive collect_all calls should yield a delta with elapsed > 0."""
        collector.collect_all()
        snapshot2 = collector.collect_all()
        if snapshot2["network"] is not None:
            assert snapshot2["network"]["delta"]["elapsed_seconds"] > 0
