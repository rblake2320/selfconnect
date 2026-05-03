"""
Tests for Vision Server cross-cutting request/error handling.
"""
import logging
from logging.handlers import RotatingFileHandler

from fastapi.testclient import TestClient

from vision_server import config
from vision_server.main import app


def test_cors_preflight_not_blocked_by_auth():
    client = TestClient(app)
    response = client.options(
        "/api/windows",
        headers={
            "Origin": f"http://localhost:{config.PORT}",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code != 401
    assert response.headers.get("access-control-allow-origin") in {
        f"http://localhost:{config.PORT}",
        "*",
    }


def test_rotating_error_log_handler_configured():
    abs_path = str(config.ERROR_LOG_PATH)
    handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, RotatingFileHandler)
    ]

    assert any(h.baseFilename == abs_path for h in handlers)
