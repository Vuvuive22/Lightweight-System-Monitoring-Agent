"""Configuration definitions for the Central Monitoring Server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Default configuration path
CONFIG_PATH = Path(__file__).parent / "server_config.json"

_DEFAULTS = {
    "port": 8000,
    "host": "0.0.0.0",
    "alert_thresholds": {
        "cpu_percent": 90.0,
        "ram_percent": 95.0,
        "disk_percent": 90.0
    },
    "anomaly_detection": {
        "enabled": True,
        "z_threshold": 2.5,
        "window_size": 30
    },
    "alert_cooldown_seconds": 300
}


def load_server_config() -> dict[str, Any]:
    """Load config from json or fallback to defaults."""
    if CONFIG_PATH.is_file():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            # Simple merge
            merged = _DEFAULTS.copy()
            for key, val in user_cfg.items():
                if isinstance(val, dict) and key in merged:
                    merged[key].update(val)
                else:
                    merged[key] = val
            return merged
        except Exception:
            pass
    return _DEFAULTS
