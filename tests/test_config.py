"""Unit tests for agent.utils.config module."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agent.utils.config import _deep_merge, _validate, load_config


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture()
def tmp_config(tmp_path: Path):
    """Helper that writes a JSON config to a temp file and returns its path."""

    def _write(data: dict[str, Any]) -> Path:
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    return _write


_MINIMAL_VALID: dict[str, Any] = {
    "interval": 30,
    "logging": {
        "mode": "file",
        "log_file_path": "/var/log/sysmon-agent/agent.log",
    },
}


# -----------------------------------------------------------------------
# _deep_merge tests
# -----------------------------------------------------------------------

class TestDeepMerge:
    """Tests for the recursive deep-merge utility."""

    def test_flat_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99}

    def test_nested_merge(self) -> None:
        base = {"logging": {"mode": "file", "max_bytes": 10}}
        override = {"logging": {"mode": "syslog"}}
        result = _deep_merge(base, override)
        assert result["logging"]["mode"] == "syslog"
        assert result["logging"]["max_bytes"] == 10

    def test_new_key_added(self) -> None:
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_does_not_mutate_defaults(self) -> None:
        defaults = {"a": {"x": 1}}
        overrides = {"a": {"y": 2}}
        _deep_merge(defaults, overrides)
        assert "y" not in defaults["a"]


# -----------------------------------------------------------------------
# _validate tests
# -----------------------------------------------------------------------

class TestValidate:
    """Tests for configuration validation rules."""

    def test_valid_minimal_config(self) -> None:
        config = _deep_merge(
            {
                "interval": 30,
                "disk_mount_points": ["/"],
                "monitored_paths": [],
                "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
                "logging": {
                    "mode": "file",
                    "log_file_path": "/var/log/sysmon-agent/agent.log",
                    "max_bytes": 10_485_760,
                    "backup_count": 5,
                    "syslog_address": "127.0.0.1",
                    "syslog_port": 514,
                    "syslog_protocol": "udp",
                },
            },
            {},
        )
        _validate(config)  # Should not raise.

    def test_interval_zero_raises(self) -> None:
        config = _deep_merge(
            {
                "interval": 0,
                "disk_mount_points": ["/"],
                "monitored_paths": [],
                "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
                "logging": {
                    "mode": "file",
                    "log_file_path": "/tmp/test.log",
                    "max_bytes": 1024,
                    "backup_count": 1,
                },
            },
            {},
        )
        with pytest.raises(ValueError, match="interval"):
            _validate(config)

    def test_negative_interval_raises(self) -> None:
        config = {
            "interval": -5,
            "disk_mount_points": ["/"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
            "logging": {"mode": "file", "log_file_path": "/tmp/t.log"},
        }
        with pytest.raises(ValueError, match="interval"):
            _validate(config)

    def test_invalid_mount_point_raises(self) -> None:
        config = {
            "interval": 10,
            "disk_mount_points": ["relative/path"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
            "logging": {"mode": "file", "log_file_path": "/tmp/t.log"},
        }
        with pytest.raises(ValueError, match="absolute path"):
            _validate(config)

    def test_invalid_logging_mode_raises(self) -> None:
        config = {
            "interval": 10,
            "disk_mount_points": ["/"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
            "logging": {"mode": "invalid"},
        }
        with pytest.raises(ValueError, match="mode"):
            _validate(config)

    def test_file_mode_empty_path_raises(self) -> None:
        config = {
            "interval": 10,
            "disk_mount_points": ["/"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
            "logging": {"mode": "file", "log_file_path": ""},
        }
        with pytest.raises(ValueError, match="log_file_path"):
            _validate(config)

    def test_syslog_mode_invalid_port_raises(self) -> None:
        config = {
            "interval": 10,
            "disk_mount_points": ["/"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
            "logging": {
                "mode": "syslog",
                "syslog_port": 99999,
                "syslog_protocol": "udp",
                "syslog_address": "127.0.0.1",
            },
        }
        with pytest.raises(ValueError, match="syslog_port"):
            _validate(config)

    def test_syslog_mode_invalid_protocol_raises(self) -> None:
        config = {
            "interval": 10,
            "disk_mount_points": ["/"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 8080, "bind_address": "0.0.0.0"},
            "logging": {
                "mode": "syslog",
                "syslog_port": 514,
                "syslog_protocol": "http",
                "syslog_address": "127.0.0.1",
            },
        }
        with pytest.raises(ValueError, match="syslog_protocol"):
            _validate(config)

    def test_dashboard_invalid_port_raises(self) -> None:
        config = {
            "interval": 10,
            "disk_mount_points": ["/"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 0, "bind_address": "0.0.0.0"},
            "logging": {"mode": "file", "log_file_path": "/tmp/t.log"},
        }
        with pytest.raises(ValueError, match="dashboard.port"):
            _validate(config)

    def test_dashboard_empty_bind_raises(self) -> None:
        config = {
            "interval": 10,
            "disk_mount_points": ["/"],
            "monitored_paths": [],
            "dashboard": {"enabled": True, "port": 8080, "bind_address": ""},
            "logging": {"mode": "file", "log_file_path": "/tmp/t.log"},
        }
        with pytest.raises(ValueError, match="bind_address"):
            _validate(config)


# -----------------------------------------------------------------------
# load_config tests
# -----------------------------------------------------------------------

class TestLoadConfig:
    """Integration tests for the full load_config pipeline."""

    def test_load_valid_config(self, tmp_config) -> None:
        path = tmp_config(_MINIMAL_VALID)
        config = load_config(path)
        assert config["interval"] == 30
        assert config["logging"]["mode"] == "file"
        # Defaults should be merged in.
        assert "disk_mount_points" in config
        assert "dashboard" in config

    def test_defaults_merged(self, tmp_config) -> None:
        path = tmp_config(_MINIMAL_VALID)
        config = load_config(path)
        # These come from _DEFAULTS, not from user config.
        assert config["disk_mount_points"] == ["/"]
        assert config["logging"]["max_bytes"] == 10_485_760
        assert config["logging"]["backup_count"] == 5

    def test_user_override_takes_precedence(self, tmp_config) -> None:
        data = {**_MINIMAL_VALID, "interval": 5}
        path = tmp_config(data)
        config = load_config(path)
        assert config["interval"] == 5

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_config(bad)

    def test_missing_required_key_raises(self, tmp_config) -> None:
        # Missing "logging" key.
        path = tmp_config({"interval": 10})
        with pytest.raises(ValueError, match="logging"):
            load_config(path)

    def test_missing_interval_key_raises(self, tmp_config) -> None:
        path = tmp_config({"logging": {"mode": "file", "log_file_path": "/tmp/t.log"}})
        with pytest.raises(ValueError, match="interval"):
            load_config(path)

    def test_syslog_config_valid(self, tmp_config) -> None:
        data = {
            "interval": 10,
            "logging": {
                "mode": "syslog",
                "syslog_address": "192.168.1.100",
                "syslog_port": 1514,
                "syslog_protocol": "tcp",
            },
        }
        path = tmp_config(data)
        config = load_config(path)
        assert config["logging"]["mode"] == "syslog"
        assert config["logging"]["syslog_port"] == 1514

    def test_dashboard_defaults_applied(self, tmp_config) -> None:
        path = tmp_config(_MINIMAL_VALID)
        config = load_config(path)
        assert config["dashboard"]["enabled"] is True
        assert config["dashboard"]["port"] == 8080
