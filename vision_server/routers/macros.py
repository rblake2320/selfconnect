"""
routers/macros.py — Macro recording and replay.
ASSIGNED TO: Agent C (Phase 4)
"""
from fastapi import APIRouter
from vision_server.models.schemas import MacroStep

router = APIRouter()


@router.post("/macros/start")
async def start_recording():
    from vision_server.services.macro_recorder import start
    start()
    return {"ok": True, "msg": "Recording started"}


@router.post("/macros/stop", response_model=list[MacroStep])
async def stop_recording():
    from vision_server.services.macro_recorder import stop
    return stop()


@router.post("/macros/replay")
async def replay_macro():
    from vision_server.services.macro_recorder import replay
    await replay()
    return {"ok": True}


@router.get("/macros", response_model=list[MacroStep])
async def get_macro():
    from vision_server.services.macro_recorder import get_steps
    return get_steps()


@router.post("/macros/export")
async def export_macro():
    from vision_server.services.macro_recorder import export_json
    path = export_json()
    return {"ok": True, "path": path}
