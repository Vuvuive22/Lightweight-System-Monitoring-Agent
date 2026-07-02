"""Unit tests for agent.utils.logger module."""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent.utils.logger import JsonFormatter, setup_logger


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

_BASE_CONFIG: dict[str, Any] = {
    "logging": {
        "mode": "file",
        "log_file_path": "",  # overridden per test
        "max_bytes": 1_048_576,
        "backup_count": 3,
        "syslog_address": "127.0.0.1",
        "syslog_port": 514,
        "syslog_protocol": "udp",
    },
}


@pytest.fixture(autouse=True)
def _clean_logger():
    """Ensure the sysmon-agent logger is fresh for every test."""
    root = logging.getLogger("sysmon-agent")
    root.handlers.clear()
    yield
    root.handlers.clear()


# -----------------------------------------------------------------------
# JsonFormatter tests
# -----------------------------------------------------------------------

class TestJsonFormatter:
    """Verify that the custom JSON formatter produces valid output."""

    def test_output_is_valid_json(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_exception_included(self) -> None:
        formatter = JsonFormatter()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="error occurred",
            args=None,
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "RuntimeError" in parsed["exception"]

    def test_module_field_present(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="sysmon-agent.metrics",
            level=logging.DEBUG,
            pathname="metrics.py",
            lineno=42,
            msg="test",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["module"] == "sysmon-agent.metrics"


# -----------------------------------------------------------------------
# setup_logger — file mode
# -----------------------------------------------------------------------

class TestSetupLoggerFileMode:
    """Tests for setup_logger when mode is 'file'."""

    def test_creates_rotating_file_handler(self, tmp_path: Path) -> None:
        log_file = tmp_path / "agent.log"
        config = {
            "logging": {
                **_BASE_CONFIG["logging"],
                "log_file_path": str(log_file),
            },
        }
        logger = setup_logger(config)
        file_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        log_file = tmp_path / "subdir" / "deep" / "agent.log"
        config = {
            "logging": {
                **_BASE_CONFIG["logging"],
                "log_file_path": str(log_file),
            },
        }
        setup_logger(config)
        assert log_file.parent.exists()

    def test_rotation_params_applied(self, tmp_path: Path) -> None:
        log_file = tmp_path / "agent.log"
        config = {
            "logging": {
                **_BASE_CONFIG["logging"],
                "log_file_path": str(log_file),
                "max_bytes": 999,
                "backup_count": 7,
            },
        }
        logger = setup_logger(config)
        handler = [
            h for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ][0]
        assert handler.maxBytes == 999
        assert handler.backupCount == 7

    def test_stderr_handler_attached(self, tmp_path: Path) -> None:
        log_file = tmp_path / "agent.log"
        config = {
            "logging": {
                **_BASE_CONFIG["logging"],
                "log_file_path": str(log_file),
            },
        }
        logger = setup_logger(config)
        stream_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(stream_handlers) == 1
        assert stream_handlers[0].level == logging.WARNING

    def test_log_level_is_debug(self, tmp_path: Path) -> None:
        log_file = tmp_path / "agent.log"
        config = {
            "logging": {
                **_BASE_CONFIG["logging"],
                "log_file_path": str(log_file),
            },
        }
        logger = setup_logger(config)
        assert logger.level == logging.DEBUG

    def test_writes_to_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "agent.log"
        config = {
            "logging": {
                **_BASE_CONFIG["logging"],
                "log_file_path": str(log_file),
            },
        }
        logger = setup_logger(config)
        logger.info("test message for file")
        # Flush handlers.
        for h in logger.handlers:
            h.flush()
        content = log_file.read_text(encoding="utf-8")
        assert "test message for file" in content


# -----------------------------------------------------------------------
# setup_logger — syslog mode
# -----------------------------------------------------------------------

class TestSetupLoggerSyslogMode:
    """Tests for setup_logger when mode is 'syslog'."""

    @patch("agent.utils.logger._build_syslog_handler")
    def test_creates_syslog_handler(self, mock_build, tmp_path: Path) -> None:
        mock_handler = logging.StreamHandler()
        mock_handler.setLevel(logging.DEBUG)
        mock_build.return_value = mock_handler
        config = {
            "logging": {
                "mode": "syslog",
                "syslog_address": "127.0.0.1",
                "syslog_port": 514,
                "syslog_protocol": "udp",
            },
        }
        setup_logger(config)
        mock_build.assert_called_once()

    def test_invalid_mode_raises(self) -> None:
        config = {"logging": {"mode": "kafka"}}
        with pytest.raises(ValueError, match="Unsupported logging mode"):
            setup_logger(config)


# -----------------------------------------------------------------------
# setup_logger — idempotency
# -----------------------------------------------------------------------

class TestSetupLoggerIdempotency:
    """Verify that calling setup_logger twice resets handlers."""

    def test_handlers_cleared_on_second_call(self, tmp_path: Path) -> None:
        log_file = tmp_path / "agent.log"
        config = {
            "logging": {
                **_BASE_CONFIG["logging"],
                "log_file_path": str(log_file),
            },
        }
        setup_logger(config)
        logger = setup_logger(config)
        # Should have exactly 2 handlers: RotatingFileHandler + StreamHandler.
        assert len(logger.handlers) == 2
