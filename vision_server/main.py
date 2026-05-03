"""
main.py — SelfConnect Vision Server
FastAPI app: REST + WebSocket, localhost only, token auth.
Port 7421 (matches dashboard default).
"""
import sys
import os
import logging
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from vision_server import config
from vision_server.routers import windows, capture, detections, vl, actions, macros, health, search, events

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
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
    if request.url.path in UNPROTECTED or request.url.path.startswith("/ws"):
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != config.SESSION_TOKEN:
        return JSONResponse(
            {"detail": "Invalid or missing token. Check the terminal for your session token."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return await call_next(request)


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
