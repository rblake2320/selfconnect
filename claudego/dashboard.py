"""
dashboard.py — FastAPI web dashboard for ClaudeGo.

Serves the single-page dashboard at localhost:9090.
Real-time updates via WebSocket.
REST endpoints for terminals, approvals, rules, and audit log.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import queue
import threading
import time
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from claudego.scanner import Scanner  # type: ignore[import]

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="ClaudeGo", version="0.1.0")

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Global scanner reference ──────────────────────────────────────────────────

_scanner: Optional[Scanner] = None
_event_queue: queue.Queue = queue.Queue()  # thread-safe bridge scanner → asyncio
_ws_clients: set[WebSocket] = set()
_ws_lock = threading.Lock()


def set_scanner(s: Scanner) -> None:
    """Called by __main__ to wire the scanner into the dashboard."""
    global _scanner
    _scanner = s
    s.add_callback(_on_scan_event)


def _on_scan_event(evt) -> None:
    """Scanner callback — called from scanner thread. Pushes to queue."""
    _event_queue.put_nowait(evt.to_dict())


# ── WebSocket broadcaster (asyncio task) ─────────────────────────────────────

async def _broadcast_loop() -> None:
    """Runs inside the FastAPI event loop. Drains queue → broadcasts to WS clients."""
    while True:
        try:
            # Non-blocking drain of the thread-safe queue
            drained: list[dict] = []
            while not _event_queue.empty():
                try:
                    drained.append(_event_queue.get_nowait())
                except queue.Empty:
                    break

            for event_dict in drained:
                payload = json.dumps(event_dict)
                dead: set[WebSocket] = set()
                with _ws_lock:
                    clients = set(_ws_clients)
                for ws in clients:
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        dead.add(ws)
                with _ws_lock:
                    _ws_clients.difference_update(dead)

        except Exception as exc:
            print(f"[claudego dashboard] broadcast error: {exc}")

        await asyncio.sleep(0.25)  # 250ms polling — low CPU, good latency


_broadcast_task = None


@app.on_event("startup")
async def _startup() -> None:
    global _broadcast_task
    _broadcast_task = asyncio.create_task(_broadcast_loop())


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    with _ws_lock:
        _ws_clients.add(ws)

    # Send current state snapshot on connect
    if _scanner:
        snapshot = {
            "event_type": "snapshot",
            "terminals": _scanner.get_terminals(),
            "audit_log": _scanner.get_audit_log(50),
            "rules": _scanner.get_rules(),
            "timestamp": time.time(),
        }
        await ws.send_text(json.dumps(snapshot))

    try:
        while True:
            # Keep connection alive — client sends pings
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard(ws)


@app.get("/api/terminals")
async def api_terminals() -> JSONResponse:
    if not _scanner:
        return JSONResponse({"terminals": [], "error": "scanner not started"})
    return JSONResponse({"terminals": _scanner.get_terminals()})


@app.post("/api/approve/{hwnd}")
async def api_approve(hwnd: int) -> JSONResponse:
    if not _scanner:
        return JSONResponse({"ok": False, "error": "scanner not started"}, status_code=503)
    ok = _scanner.manual_approve(hwnd)
    return JSONResponse({"ok": ok, "hwnd": hwnd})


@app.post("/api/deny/{hwnd}")
async def api_deny(hwnd: int) -> JSONResponse:
    if not _scanner:
        return JSONResponse({"ok": False, "error": "scanner not started"}, status_code=503)
    ok = _scanner.manual_deny(hwnd)
    return JSONResponse({"ok": ok, "hwnd": hwnd})


@app.get("/api/rules")
async def api_get_rules() -> JSONResponse:
    if not _scanner:
        return JSONResponse({"error": "scanner not started"}, status_code=503)
    return JSONResponse(_scanner.get_rules())


@app.post("/api/rules")
async def api_set_rules(body: dict) -> JSONResponse:
    if not _scanner:
        return JSONResponse({"error": "scanner not started"}, status_code=503)
    allow = body.get("allow", [])
    deny = body.get("deny", [])
    default_action = body.get("default_action", "escalate")
    _scanner.set_rules(allow=allow, deny=deny, default_action=default_action)
    return JSONResponse({"ok": True})


@app.get("/api/log")
async def api_log(limit: int = 100) -> JSONResponse:
    if not _scanner:
        return JSONResponse({"log": []})
    return JSONResponse({"log": _scanner.get_audit_log(limit)})


@app.get("/api/health")
async def api_health() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "scanner": _scanner is not None,
        "ws_clients": len(_ws_clients),
        "timestamp": time.time(),
    })
