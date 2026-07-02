"""Configuration loader and validator for sysmon-agent."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "interval": 30,
    "disk_mount_points": ["/"],
    "monitored_paths": [],
    "dashboard": {
        "enabled": True,
        "port": 8080,
        "bind_address": "0.0.0.0",
    },
    "logging": {
        "mode": "file",
        "log_file_path": "/var/log/sysmon-agent/agent.log",
        "max_bytes": 10_485_760,
        "backup_count": 5,
        "syslog_address": "127.0.0.1",
        "syslog_port": 514,
        "syslog_protocol": "udp",
    },
}

_REQUIRED_KEYS: list[str] = ["interval", "logging"]


def _deep_merge(defaults: dict, overrides: dict) -> dict:
    """Recursively merge *overrides* into *defaults*."""
    merged: dict[str, Any] = defaults.copy()
    for key, value in overrides.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _validate(config: dict) -> None:
    """Raise ``ValueError`` when the configuration is invalid."""
    # --- interval ---
    interval = config.get("interval")
    if not isinstance(interval, (int, float)) or interval <= 0:
        raise ValueError(
            f"'interval' must be a positive number, got {interval!r}"
        )

    # --- disk_mount_points ---
    mount_points = config.get("disk_mount_points", [])
    if not isinstance(mount_points, list):
        raise ValueError("'disk_mount_points' must be a list of path strings")
    import platform
    import os
    is_windows = platform.system() == "Windows"
    for mp in mount_points:
        if not isinstance(mp, str):
            raise ValueError("Each disk_mount_points entry must be a path string")
        if is_windows:
            if not os.path.isabs(mp) and not mp.startswith("/"):
                raise ValueError(
                    f"Each disk_mount_points entry must be an absolute path, "
                    f"got {mp!r}"
                )
        else:
            if not mp.startswith("/"):
                raise ValueError(
                    f"Each disk_mount_points entry must be an absolute path, "
                    f"got {mp!r}"
                )

    # --- monitored_paths ---
    monitored = config.get("monitored_paths", [])
    if not isinstance(monitored, list):
        raise ValueError("'monitored_paths' must be a list of path strings")

    # --- logging section ---
    log_cfg = config.get("logging")
    if not isinstance(log_cfg, dict):
        raise ValueError("'logging' section must be a dictionary")

    mode = log_cfg.get("mode", "")
    if mode not in ("file", "syslog"):
        raise ValueError(
            f"'logging.mode' must be 'file' or 'syslog', got {mode!r}"
        )

    if mode == "file":
        log_path = log_cfg.get("log_file_path", "")
        if not isinstance(log_path, str) or not log_path:
            raise ValueError(
                "'logging.log_file_path' must be a non-empty string "
                "when mode is 'file'"
            )

    if mode == "syslog":
        port = log_cfg.get("syslog_port", 0)
        if not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError(
                f"'logging.syslog_port' must be 1-65535, got {port!r}"
            )
        proto = log_cfg.get("syslog_protocol", "")
        if proto not in ("udp", "tcp"):
            raise ValueError(
                f"'logging.syslog_protocol' must be 'udp' or 'tcp', "
                f"got {proto!r}"
            )

    # --- dashboard section ---
    db_cfg = config.get("dashboard", {})
    if not isinstance(db_cfg, dict):
        raise ValueError("'dashboard' section must be a dictionary")

    enabled = db_cfg.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("'dashboard.enabled' must be a boolean")

    port = db_cfg.get("port", 8080)
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError(
            f"'dashboard.port' must be 1-65535, got {port!r}"
        )

    bind_addr = db_cfg.get("bind_address", "0.0.0.0")
    if not isinstance(bind_addr, str) or not bind_addr:
        raise ValueError(
            "'dashboard.bind_address' must be a non-empty string"
        )


def load_config(path: str | Path) -> dict[str, Any]:
    """Load, validate, and return the agent configuration.

    Parameters
    ----------
    path:
        Filesystem path to the JSON configuration file.

    Returns
    -------
    dict
        Validated configuration dictionary with defaults applied for
        any missing optional fields.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    json.JSONDecodeError
        If the file is not valid JSON.
    ValueError
        If required fields are missing or values are invalid.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    raw_text = config_path.read_text(encoding="utf-8")
    user_config: dict[str, Any] = json.loads(raw_text)

    # Check required top-level keys exist in user input.
    for key in _REQUIRED_KEYS:
        if key not in user_config:
            raise ValueError(f"Missing required config key: '{key}'")

    config = _deep_merge(_DEFAULTS, user_config)
    _validate(config)

    logger.info("Configuration loaded from %s", config_path)
    return config
