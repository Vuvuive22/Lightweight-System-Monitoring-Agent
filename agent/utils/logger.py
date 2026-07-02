"""Unified logging setup with file rotation and syslog routing."""

from __future__ import annotations

import json
import logging
import logging.handlers
import socket
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    """Custom logging formatter that serializes log records into valid escape-safe JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)

_SYSLOG_FORMAT = "sysmon-agent[%(process)d]: %(levelname)s %(name)s - %(message)s"


def setup_logger(config: dict[str, Any]) -> logging.Logger:
    """Create and return a configured root logger for the agent.

    Parameters
    ----------
    config:
        The full agent configuration dictionary.  The ``logging`` sub-dict
        controls handler selection and parameters.

    Returns
    -------
    logging.Logger
        A logger named ``sysmon-agent`` with the appropriate handler
        attached.

    Raises
    ------
    ValueError
        If ``logging.mode`` is not ``'file'`` or ``'syslog'``.
    OSError
        If the log directory cannot be created or the syslog socket is
        unreachable.
    """
    log_cfg: dict[str, Any] = config["logging"]
    mode: str = log_cfg["mode"]

    root_logger = logging.getLogger("sysmon-agent")
    root_logger.setLevel(logging.DEBUG)

    # Remove any pre-existing handlers (useful when reloading config).
    root_logger.handlers.clear()

    if mode == "file":
        handler = _build_file_handler(log_cfg)
    elif mode == "syslog":
        handler = _build_syslog_handler(log_cfg)
    else:
        raise ValueError(f"Unsupported logging mode: {mode!r}")

    root_logger.addHandler(handler)

    # Also attach a lightweight stderr handler so early startup errors
    # are visible in journalctl when running under systemd.
    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter(_SYSLOG_FORMAT))
    root_logger.addHandler(stderr_handler)

    root_logger.info("Logger initialised — mode=%s", mode)
    return root_logger


# -------------------------------------------------------------------
# Handler builders
# -------------------------------------------------------------------

def _build_file_handler(
    log_cfg: dict[str, Any],
) -> logging.handlers.RotatingFileHandler:
    """Return a ``RotatingFileHandler`` configured from *log_cfg*."""
    log_path = Path(log_cfg["log_file_path"])

    # Ensure the parent directory exists.
    log_path.parent.mkdir(parents=True, exist_ok=True)

    max_bytes: int = int(log_cfg.get("max_bytes", 10_485_760))
    backup_count: int = int(log_cfg.get("backup_count", 5))

    handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(JsonFormatter())
    return handler


def _build_syslog_handler(
    log_cfg: dict[str, Any],
) -> logging.handlers.SysLogHandler:
    """Return a ``SysLogHandler`` configured from *log_cfg*.

    If *syslog_address* is ``"/dev/log"`` or another Unix socket path
    the handler connects locally; otherwise it uses UDP or TCP to the
    specified ``(address, port)`` pair.
    """
    address: str = log_cfg.get("syslog_address", "127.0.0.1")
    port: int = int(log_cfg.get("syslog_port", 514))
    protocol: str = log_cfg.get("syslog_protocol", "udp").lower()

    # Local Unix socket (e.g. /dev/log).
    if address.startswith("/"):
        handler = logging.handlers.SysLogHandler(
            address=address,
            facility=logging.handlers.SysLogHandler.LOG_DAEMON,
        )
    else:
        sock_type = (
            socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
        )
        handler = logging.handlers.SysLogHandler(
            address=(address, port),
            facility=logging.handlers.SysLogHandler.LOG_DAEMON,
            socktype=sock_type,
        )

    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_SYSLOG_FORMAT))
    return handler
