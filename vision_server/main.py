"""
main.py — SelfConnect Vision Server
FastAPI app: REST + WebSocket, localhost only, token auth.
Port 7421 (matches dashboard default).
"""
import sys
import os
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vision_server import config
from vision_server.routers import windows, capture, detections, vl, actions, macros, health, search, events

def _configure_logging() -> None:
    """Configure console logging plus a durable rotating error log."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        root.addHandler(console)

    abs_error_log = os.path.abspath(config.ERROR_LOG_PATH)
    if not any(
        isinstance(h, RotatingFileHandler)
        and os.path.abspath(getattr(h, "baseFilename", "")) == abs_error_log
        for h in root.handlers
    ):
        file_handler = RotatingFileHandler(
            abs_error_log,
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.ERROR)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


_configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    print("=" * 60)
    print("SelfConnect Vision Server v1.0")
    print(f"  URL:   http://{config.HOST}:{config.PORT}")
    print(f"  Token: {config.SESSION_TOKEN}")
    print("  Copy the token into the dashboard connection panel.")
    print("=" * 60)

    # Start background services
    from vision_server.services import capture_service, health_monitor, detection_service
    await capture_service.start()
    await health_monitor.start()
    await detection_service.start_detection_loop()

    yield

    # Shutdown
    await capture_service.stop()
    await health_monitor.stop()
    logger.info("Server stopped.")


app = FastAPI(title="SelfConnect Vision Server", version="1.0.0", lifespan=lifespan)

# CORS — restrict to local file and localhost origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",                        # file:// origin
        f"http://localhost:{config.PORT}",
        f"http://127.0.0.1:{config.PORT}",
        "http://localhost:5500",        # VS Code Live Server
        "http://127.0.0.1:5500",
        "http://localhost:8080",        # python -m http.server
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth middleware ────────────────────────────────────────────────────────────

UNPROTECTED = {"/", "/api/health", "/docs", "/openapi.json", "/redoc"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require Bearer token on all routes except health and docs."""
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path in UNPROTECTED or request.url.path.startswith("/ws"):
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != config.SESSION_TOKEN:
        return JSONResponse(
            {"detail": "Invalid or missing token. Check the terminal for your session token."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return await call_next(request)


@app.middleware("http")
async def error_logging_middleware(request: Request, call_next):
    """Log unhandled server exceptions to the durable error log."""
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled request error: %s %s", request.method, request.url.path)
        return JSONResponse(
            {"detail": "Internal server error. See server error log."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(windows.router,    prefix="/api")
app.include_router(capture.router)             # includes WebSocket
app.include_router(detections.router, prefix="/api")
app.include_router(vl.router,         prefix="/api")
app.include_router(actions.router,    prefix="/api")
app.include_router(macros.router,     prefix="/api")
app.include_router(health.router,     prefix="/api")
app.include_router(search.router,     prefix="/api")
app.include_router(events.router)              # WebSocket /ws/events


@app.get("/")
async def root():
    return {
        "service": "SelfConnect Vision Server",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health",
    }
