"""Unit tests for agent.utils.dashboard module."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest

from agent.utils.dashboard import DashboardServer


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

def _make_config(
    enabled: bool = True,
    port: int = 18080,
    bind_address: str = "127.0.0.1",
) -> dict[str, Any]:
    """Build a test configuration dict."""
    return {
        "dashboard": {
            "enabled": enabled,
            "port": port,
            "bind_address": bind_address,
        },
        "logging": {
            "mode": "file",
            "log_file_path": "/tmp/test-dashboard-agent.log",
        },
    }


# -----------------------------------------------------------------------
# Initialization tests
# -----------------------------------------------------------------------

class TestDashboardInit:
    """Tests for DashboardServer initialization."""

    def test_reads_config_values(self) -> None:
        cfg = _make_config(enabled=True, port=9999, bind_address="0.0.0.0")
        server = DashboardServer(cfg)
        assert server.enabled is True
        assert server.port == 9999
        assert server.bind_address == "0.0.0.0"

    def test_is_daemon_thread(self) -> None:
        server = DashboardServer(_make_config())
        assert server.daemon is True

    def test_thread_name(self) -> None:
        server = DashboardServer(_make_config())
        assert server.name == "sysmon-dashboard-thread"

    def test_log_file_path_from_config(self) -> None:
        cfg = _make_config()
        cfg["logging"]["log_file_path"] = "/var/log/custom/agent.log"
        server = DashboardServer(cfg)
        assert server.log_file_path == "/var/log/custom/agent.log"

    def test_default_log_file_path(self) -> None:
        cfg: dict[str, Any] = {"dashboard": {"enabled": True, "port": 8080, "bind_address": "127.0.0.1"}}
        server = DashboardServer(cfg)
        assert server.log_file_path == "/var/log/sysmon-agent/agent.log"

    def test_defaults_when_dashboard_section_missing(self) -> None:
        cfg: dict[str, Any] = {"logging": {"log_file_path": "/tmp/t.log"}}
        server = DashboardServer(cfg)
        assert server.enabled is True
        assert server.port == 8080
        assert server.bind_address == "0.0.0.0"


# -----------------------------------------------------------------------
# Disabled mode
# -----------------------------------------------------------------------

class TestDashboardDisabled:
    """Tests for when the dashboard is disabled in config."""

    def test_disabled_server_does_not_listen(self) -> None:
        server = DashboardServer(_make_config(enabled=False))
        server.start()
        # Give it a moment — it should exit its run() immediately.
        time.sleep(0.3)
        # _httpd should remain None since run() returns early.
        assert server._httpd is None
        server.stop()


# -----------------------------------------------------------------------
# Start / Stop lifecycle
# -----------------------------------------------------------------------

class TestDashboardLifecycle:
    """Integration tests for server start and stop."""

    def test_start_and_stop(self) -> None:
        """Server should bind to port and stop cleanly."""
        # Use a high ephemeral port to avoid conflicts.
        server = DashboardServer(_make_config(port=19876, bind_address="127.0.0.1"))
        server.start()
        time.sleep(0.5)
        assert server._httpd is not None
        server.stop()
        time.sleep(0.3)

    def test_stop_without_start_is_safe(self) -> None:
        server = DashboardServer(_make_config())
        server.stop()  # Should not raise.
