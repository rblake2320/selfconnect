# SelfConnect Vision Server — Architecture Reference

## Overview

The Vision Server adds AI perception (what's on screen) to the SelfConnect SDK's existing AI
action capabilities (click, type, key). Together they form a complete desktop automation loop:

```
Capture → Detect → Describe → Decide → Act
```

---

## System Diagram

```
Browser (vision_agent_dashboard.html)
   ├── REST    GET/POST /api/*          (token auth required)
   ├── WS      ws://localhost:7421/ws/capture   (binary JPEG stream, no auth)
   └── WS      ws://localhost:7421/ws/events    (JSON multiplexed, no auth)
                    │
FastAPI Server  vision_server/  port 7421
   ├── routers/          REST + WebSocket handlers
   ├── services/         Background loops, inference, SDK wrappers
   └── models/schemas.py Pydantic contracts matching dashboard shapes

SelfConnect SDK  self_connect.py
   └── Win32 ctypes: list_windows, capture_window, click_at, send_string, send_keys

Ollama  port 11434
   └── llava:7b — VL inference for screen description and structured UI detection
```

---

## File Structure

```
selfconnect/
  self_connect.py                  Core SDK (Win32 window control — unchanged)
  vision_agent_dashboard.html      Browser UI (React+Tailwind, no build step)
  run_server.py                    Entry point: uvicorn on port 7421

  vision_server/
    __init__.py
    main.py                        FastAPI app, CORS, auth middleware, lifespan
    config.py                      All tuneable values (ports, FPS, models, paths)
    routers/
      windows.py                   GET /api/windows, POST /api/windows/{hwnd}/attach
      capture.py                   WS /ws/capture + GET /api/capture/{hwnd}
      detections.py                GET /api/detections
      vl.py                        POST /api/vl/describe
      actions.py                   POST /api/actions, GET /api/queue, POST /api/actions/run
      macros.py                    POST /api/macros/start|stop|replay|export
      health.py                    GET /api/health
      search.py                    POST /api/search (stub — 501, nvclip not implemented)
      events.py                    WS /ws/events (multiplexed)
    services/
      capture_service.py           Background capture loop → WS push
      detection_service.py         UI element detection (Win32 + llava fallback)
      vl_service.py                Ollama llava HTTP client
      action_queue.py              FIFO queue with state machine
      macro_recorder.py            Record/replay action sequences
      event_bus.py                 In-process pub/sub (asyncio)
      health_monitor.py            Periodic service health checks
    models/
      schemas.py                   Pydantic models (dashboard API contract)

  tests/
    test_schemas.py                Schema validation unit tests
    test_event_bus.py              Pub/sub unit tests
    test_action_queue.py           Queue state machine unit tests

  docs/
    DEPLOY.md                      Installation and deployment guide
    ARCHITECTURE.md                This file
```

---

## API Reference

### REST Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | Service info |
| GET | `/api/health` | No | Health status of all services |
| GET | `/api/windows` | Yes | List all visible windows |
| POST | `/api/windows/{hwnd}/attach` | Yes | Set active capture target |
| GET | `/api/capture/{hwnd}` | Yes | Single JPEG frame (base64) |
| GET | `/api/detections` | Yes | Latest detection results |
| POST | `/api/vl/describe` | Yes | Trigger VL screen description |
| POST | `/api/actions` | Yes | Enqueue an action |
| GET | `/api/queue` | Yes | Current queue state |
| POST | `/api/actions/run` | Yes | Start executing the queue |
| POST | `/api/macros/start` | Yes | Begin recording |
| POST | `/api/macros/stop` | Yes | Stop recording, return steps |
| POST | `/api/macros/replay` | Yes | Replay recorded macro |
| POST | `/api/macros/export` | Yes | Export macro as JSON |
| POST | `/api/search` | Yes | Semantic element search (stub) |

### WebSocket Endpoints

| Path | Auth | Description |
|------|------|-------------|
| `/ws/capture` | No | Binary JPEG stream (15fps target) |
| `/ws/events` | No | JSON multiplexed channel stream |

**Note on WS auth:** Browsers cannot set custom headers on WebSocket connections. Both WS
endpoints are on the UNPROTECTED list server-side. Security is provided by the `127.0.0.1`
bind and CORS origin restriction — no external host can reach the server.

### /ws/events Channel Messages

Every message is a JSON object `{ "channel": "<name>", "data": <payload> }`:

| Channel | Data shape | Published by |
|---------|-----------|--------------|
| `detections` | `Detection[]` | detection_service (every ~2s) |
| `log` | `LogEntry` | Any service (real-time) |
| `queue` | `QueueItem[]` | action_queue (on state change) |
| `health` | `HealthStatus` | health_monitor (every 5s) |
| `windows` | `WindowInfo[]` | windows router (on attach) |
| `vl` | `VLDescription` | vl_service (on describe) |

---

## Service Architecture

### Capture Service (`services/capture_service.py`)

Two capture methods, tried in order:

1. **`capture_window(hwnd)`** via Win32 `PrintWindow` — works for native Win32 apps,
   minimized windows, and occluded windows.
2. **`PIL.ImageGrab.grab(bbox)`** — fallback for GPU-composited windows (Chrome, Edge,
   Electron) where `PrintWindow` returns a black frame. Requires the window to be visible.

The service checks `CAPTURE_MIN_NONZERO` (default 1% non-zero pixels) to detect an all-black
frame and trigger the fallback automatically.

Frames are JPEG-encoded and pushed as binary WebSocket messages to all `/ws/capture` clients.

### Detection Service (`services/detection_service.py`)

Dual strategy based on window type:

- **Native Win32 apps** (Notepad, File Explorer, Office): `list_child_controls(hwnd)` returns
  exact control rects from `EnumChildWindows`. High accuracy, zero ML cost.
- **Browser/Electron windows** (Chrome, VS Code, etc.): `EnumChildWindows` only sees the browser
  shell. Detection falls back to Ollama llava with a structured prompt asking it to list UI
  elements with coordinates. ~1–2s per query.

**Important:** Stock YOLOv8 (COCO-trained) detects real-world objects, not UI elements. It is
NOT used in v1. When a custom UI-detection YOLO model becomes available, it will slot in here.
The extension point is documented in `detection_service.py`.

### VL Service (`services/vl_service.py`)

Calls `POST /api/generate` on Ollama with `llava:7b` (or `SC_VL_MODEL`). Non-streaming
request with 300s timeout (llava cold start can take 120–180s on first load). Returns a
`VLDescription` with the description text, extracted tags, token count, and elapsed ms.

### Action Queue (`services/action_queue.py`)

FIFO queue with per-item state machine: `pending → running → done | failed`.

SDK calls (`click_at`, `send_string`, `send_keys`, `scroll_window`) are synchronous Win32
operations. They run in `loop.run_in_executor(None, ...)` to avoid blocking the async event loop.

Focus verification: before each `send_string`, `focus_window(hwnd)` is called and the queue
waits 100ms. This reduces keystroke mis-targeting when the user clicks away mid-run.

### Event Bus (`services/event_bus.py`)

In-process asyncio pub/sub. Any service publishes to a channel; the `/ws/events` WebSocket
handler subscribes to all channels and forwards messages to connected dashboard clients.

Dead subscriber cleanup: if a subscriber raises during delivery, it is removed automatically
(handles WebSocket disconnects without crashing the publisher).

### Health Monitor (`services/health_monitor.py`)

Runs a background asyncio task checking every 5 seconds:

- **sdk**: calls `list_windows()` in a thread executor
- **vl**: `GET /api/tags` on Ollama (3s timeout)
- **yolo**: always `degraded` in v1 (no custom model)
- **claude**: always `ok` (placeholder; would check API key validity)

Publishes to `health` channel on every check cycle.

---

## Security Model

1. **Localhost-only bind** — `HOST = "127.0.0.1"` in `config.py`. The server cannot be reached
   from the network even if the firewall is open.
2. **Bearer token auth** — Random `secrets.token_urlsafe(24)` generated at startup. All REST
   routes require `Authorization: Bearer <token>`. Printed once to terminal on startup.
3. **CORS origin restriction** — Only `null` (file://), `localhost:7421`, `127.0.0.1:7421`,
   and `localhost:5500` are allowed. Other browser tabs on different origins cannot call the API.
4. **WS unprotected by design** — Browsers cannot set `Authorization` headers on WebSocket
   connections. Security is handled by #1 and #3 instead.

---

## Threading Model

```
asyncio event loop (main thread)
  ├── FastAPI request handlers
  ├── WebSocket handlers (/ws/capture, /ws/events)
  ├── health_monitor._monitor_loop()     background task
  ├── capture_service._capture_loop()    background task
  ├── detection_service._detection_loop() background task
  └── action_queue._execute_loop()       background task (when running)

ThreadPoolExecutor (default pool)
  ├── list_windows()        — called from health_monitor, windows router
  ├── capture_window()      — called from capture_service
  ├── click_at()            — called from action_queue
  ├── send_string()         — called from action_queue
  └── send_keys()           — called from action_queue
```

All Win32 ctypes calls are synchronous. They run in `loop.run_in_executor(None, fn, *args)` to
avoid blocking the event loop. The default ThreadPoolExecutor is used (Python default: min(32, cpu+4) workers).

---

## Known Limitations

| Limitation | Impact | Future fix |
|-----------|--------|-----------|
| Browser/Electron UI detection via llava | ~1–2s latency per query; imprecise coordinates | Custom UI-YOLO model |
| Ollama llava cold start | 30–180s first inference after model unloaded | Keep-alive or warm-up call at server start |
| `send_string` focus dependency | Keystrokes go to wrong window if user clicks away | OS-level input injection hook |
| ImageGrab requires visible window | GPU-composited windows must be on screen | DirectX/DXGI capture (not yet implemented) |
| Windows-only | Win32 ctypes, PIL.ImageGrab, Win32 SendInput | Linux/macOS SDK would need platform abstraction |

---

## Extension Points

### Adding a custom UI-YOLO model

1. `pip install ultralytics torch torchvision --index-url https://download.pytorch.org/whl/cu124`
2. In `detection_service.py`, replace the stub YOLO section with:
   ```python
   from ultralytics import YOLO
   _yolo = YOLO("models/ui_yolo.pt")
   results = _yolo(frame_np, imgsz=640, conf=config.DETECTION_VL_CONFIDENCE)
   ```
3. Update `config.py` to add `YOLO_MODEL_PATH`.
4. Update `health_monitor.py` to check `_yolo` load status.

### Enabling nvclip semantic search

The `/api/search` stub returns 501. To enable:
1. Deploy nvclip NIM (WSL2 + Podman + NGC key) on port 8000.
2. Implement `services/nvclip_service.py` using `httpx` to call the NIM endpoint.
3. Wire it into `routers/search.py`.
