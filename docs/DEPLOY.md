# SelfConnect Vision Server — Deployment Guide

**Target platform:** Windows 10/11 with NVIDIA GPU

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | `python --version` |
| NVIDIA GPU | Any CUDA-capable | RTX 3000+ recommended for llava:7b |
| Ollama | 0.3+ | [ollama.com](https://ollama.com) |
| Git | Any | To clone the repo |

---

## 1. Clone the repository

```bat
git clone https://github.com/your-org/selfconnect.git
cd selfconnect
```

---

## 2. Create a virtual environment

```bat
python -m venv .venv
.venv\Scripts\activate
```

---

## 3. Install Python dependencies

```bat
pip install -r requirements-vision.txt
```

> **Note:** `ultralytics` (YOLOv8) is NOT in `requirements-vision.txt`. It is a planned future
> extension for custom UI detection models. The current detection pipeline uses Win32
> `EnumChildWindows` (for native apps) and Ollama llava (for browser/Electron windows).

---

## 4. Install the VL model in Ollama

```bat
ollama pull llava:7b
```

Verify it loaded:

```bat
ollama list
```

Expected output includes `llava:7b`.

**GPU memory note:** `llava:7b` uses approximately 6–8 GB VRAM. If you have other large models
loaded (e.g. a 30B+ LLM), stop them first:

```bat
ollama stop <model-name>
```

---

## 5. Start the server

```bat
python run_server.py
```

On startup you will see:

```
============================================================
SelfConnect Vision Server v1.0
  URL:   http://127.0.0.1:7421
  Token: <random-32-char-token>
  Copy the token into the dashboard connection panel.
============================================================
```

Copy the token — you will need it to connect the dashboard.

**Override the token** (for scripted/automated clients):

```bat
set SC_TOKEN=my-fixed-token
python run_server.py
```

**Override the VL model:**

```bat
set SC_VL_MODEL=llava:13b
python run_server.py
```

---

## 6. Open the dashboard

Open `vision_agent_dashboard.html` directly in your browser (double-click or `file://` URL).

1. The **Config** panel opens automatically if no token is stored.
2. Paste the token from the terminal.
3. Click **Connect**.

The status bar should show **Connected** within 1–2 seconds.

> **VS Code Live Server:** If using the Live Server extension, it serves on `http://localhost:5500`.
> This origin is already in the CORS allow-list. No changes needed.

---

## 7. Verify the installation

Work through this checklist top-to-bottom:

- [ ] **Health panel** shows `SDK: ok`, `VL: ok`
- [ ] **RESCAN** button populates the window list with real windows
- [ ] **ATTACH** on any window → live capture stream appears in the viewer
- [ ] **Look** button → VL description appears in AI Understanding panel (allow 10–60s on cold start)
- [ ] Type `click Submit` in the command bar → item appears in the action queue
- [ ] **RUN** → action executes (window must be focused)
- [ ] **RECORD** → perform some clicks/types → **STOP** → steps appear in Macro panel
- [ ] **REPLAY** → macro replays the recorded steps

---

## Troubleshooting

### Server won't start — "address already in use"

Another process is on port 7421. Either stop it or change `PORT` in `vision_server/config.py`.

```bat
netstat -ano | findstr 7421
taskkill /PID <pid> /F
```

### Dashboard shows "Disconnected"

1. Confirm the server is running (`python run_server.py` in a terminal).
2. Check the token — paste it fresh from the terminal into the Config panel.
3. Check browser console (F12) for CORS or connection errors.

### Capture shows all black

The window is GPU-composited (Chrome, Edge, most Electron apps) and `PrintWindow` returned a black
frame. The capture service automatically falls back to `PIL.ImageGrab.grab(bbox)`. **For
ImageGrab to work, the window must be:**

- Visible on screen (not minimized or behind another window)
- On the primary monitor (for single-monitor setups, this is always true)

Move the target window to the front if the capture stays black.

### VL / "Look" returns an error

1. Confirm Ollama is running: open a browser and visit `http://localhost:11434/api/tags`
2. Confirm `llava:7b` is listed: `ollama list`
3. Check VRAM — if a large model is loaded, stop it: `ollama stop <model>`
4. First inference after a cold start can take 30–90 seconds. The dashboard will wait.

### Bounding boxes don't appear on browser windows

This is expected behavior. `EnumChildWindows` (the Win32 detection method) cannot see DOM
elements inside Chrome, Edge, or Electron apps. For those windows, detection uses llava structured
prompts (click **Look** to trigger it). A custom UI-detection YOLO model is a planned future
enhancement — see `docs/ARCHITECTURE.md`.

---

## Running as a background service (optional)

Use NSSM to run the server as a Windows service:

```bat
nssm install SelfConnectVision "C:\path\to\.venv\Scripts\python.exe" "C:\path\to\run_server.py"
nssm set SelfConnectVision AppDirectory "C:\path\to\selfconnect"
nssm set SelfConnectVision AppEnvironmentExtra "SC_TOKEN=my-fixed-token"
nssm start SelfConnectVision
```

Set a fixed `SC_TOKEN` when running as a service so the token is stable across restarts.

---

## Environment variables reference

| Variable | Default | Description |
|----------|---------|-------------|
| `SC_TOKEN` | random | Session token for Bearer auth |
| `SC_VL_MODEL` | `llava:7b` | Ollama model for VL inference |
