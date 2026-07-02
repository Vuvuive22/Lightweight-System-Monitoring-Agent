"""sysmon-agent — HTTP Dashboard Server.

Serves a lightweight local web dashboard for monitoring system metrics
and file system events visually. Uses only the Python standard library.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("sysmon-agent.dashboard")


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for serving the dashboard and API endpoints."""

    # Disable default request logging to stdout to keep sysmon-agent logs clean.
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        """Route incoming GET requests."""
        try:
            if self.path in ("/", "/index.html"):
                self._serve_html()
            elif self.path == "/api/logs":
                self._serve_api_logs()
            elif self.path == "/api/config":
                self._serve_api_config()
            else:
                self.send_error(404, "File Not Found")
        except Exception as e:
            logger.exception("Error processing HTTP request: %s", self.path)
            self.send_error(500, f"Internal Server Error: {str(e)}")

    def _serve_html(self) -> None:
        """Serve the dashboard.html file."""
        html_path = Path(__file__).parent / "dashboard.html"
        if not html_path.is_file():
            self.send_error(404, "dashboard.html template not found")
            return

        try:
            content = html_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Failed to read template: {str(e)}")

    def _serve_api_config(self) -> None:
        """Serve current agent configuration."""
        config = self.server.agent_config  # type: ignore
        response_bytes = json.dumps(config).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _serve_api_logs(self) -> None:
        """Parse the agent.log file and serve parsed JSON logs (last 200 lines)."""
        log_path = Path(self.server.log_file_path)  # type: ignore
        logs = []

        if log_path.is_file():
            try:
                # Read the file contents.
                # Since log rotation is enabled, the active log file is capped (e.g. 10MB).
                # Reading all lines is safe and simple.
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                
                # Take the last 200 lines and parse JSON log records.
                for line in reversed(all_lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        log_record = json.loads(line)
                        logs.append(log_record)
                        if len(logs) >= 200:
                            break
                    except json.JSONDecodeError:
                        # Ignore lines that aren't valid JSON (e.g. startup banners or syslog format)
                        continue
            except Exception as e:
                logger.error("Failed to read log file at %s: %s", log_path, e)

        response_bytes = json.dumps(logs).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)


class DashboardServer(threading.Thread):
    """Background thread running the lightweight HTTP server for the dashboard."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.daemon = True
        self.name = "sysmon-dashboard-thread"
        self._config = config
        
        # Determine log file path from config
        logging_config = config.get("logging", {})
        self.log_file_path = logging_config.get(
            "log_file_path", "/var/log/sysmon-agent/agent.log"
        )

        # Dashboard settings (default: enabled=True, port=8080, bind_address=0.0.0.0)
        dashboard_config = config.get("dashboard", {})
        self.enabled = dashboard_config.get("enabled", True)
        self.port = int(dashboard_config.get("port", 8080))
        self.bind_address = dashboard_config.get("bind_address", "0.0.0.0")
        
        self._httpd: HTTPServer | None = None

    def run(self) -> None:
        """Start the HTTP server loop."""
        if not self.enabled:
            logger.info("Dashboard server is disabled in config")
            return

        try:
            self._httpd = HTTPServer(
                (self.bind_address, self.port), DashboardHTTPHandler
            )
            # Attach context attributes to the server instance to access from handlers
            self._httpd.agent_config = self._config  # type: ignore
            self._httpd.log_file_path = self.log_file_path  # type: ignore
            
            logger.info("Dashboard server listening on http://%s:%d", self.bind_address, self.port)
            self._httpd.serve_forever()
        except Exception as e:
            logger.error("Failed to start dashboard server on port %d: %s", self.port, e)

    def stop(self) -> None:
        """Stop the HTTP server loop and release sockets."""
        if self._httpd:
            logger.info("Stopping dashboard server ...")
            # shutdown() will stop serve_forever() loop from another thread.
            self._httpd.shutdown()
            self._httpd.server_close()
            logger.info("Dashboard server stopped")
