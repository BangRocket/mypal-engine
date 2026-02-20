"""Health check HTTP server for container orchestration."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)

# Global state for health checks
backup_state: dict = {
    "status": "starting",
    "last_backup": None,
    "last_error": None,
    "backups_completed": 0,
}


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP handler for health check endpoints."""

    def log_message(self, format, *args):
        pass  # Suppress default request logging

    def do_GET(self):
        if self.path in ("/health", "/"):
            self._respond(200, {"status": "healthy", **backup_state})
        elif self.path == "/ready":
            if backup_state["status"] in ("ready", "completed", "running"):
                self._respond(200, {"ready": True})
            else:
                self._respond(503, {"ready": False, "status": backup_state["status"]})
        elif self.path == "/live":
            self._respond(200, {"alive": True})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def start_health_server(port: int) -> HTTPServer:
    """Start health check HTTP server in a daemon thread."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health server started on port {port}")
    return server
