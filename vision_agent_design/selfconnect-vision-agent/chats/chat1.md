# SelfConnect Vision Agent

_Started 2026-05-01 15:35 UTC_

---

## User

Build a React dashboard for "SelfConnect Vision Agent" — an AI desktop automation system.

  Layout: Dark theme, single page, 3-column grid.

  LEFT COLUMN (40%):
  - Live window capture viewer (shows screenshots as they stream in via WebSocket on ws://localhost:8080/feed)
  - YOLO detection overlay: colored bounding boxes around detected UI elements (buttons=green, text fields=blue, labels=gray, images=yellow)
  - Clicking on a detection box should emit the element info to the action log

  CENTER COLUMN (35%):
  - "AI Understanding" panel: shows the VL model's text interpretation of the current screen ("I see a login form with email and password
  fields, a Submit button, and a 'Forgot password' link")
  - Action Queue: list of pending actions (click, type, navigate) with play/pause/cancel
  - Macro Recorder: record/stop/replay buttons. When recording, shows a live list of captured actions (timestamp, action type, target, value)

  RIGHT COLUMN (25%):
  - Target Windows: list of discovered windows (hwnd, title, exe) with select button
  - Action Log: scrollable timestamped log of all actions taken (green=success, red=fail, yellow=pending)
  - Connection status indicators for: SelfConnect SDK, YOLO model, VL model (Ollama), Claude API

  TOP BAR:
  - "SelfConnect Vision Agent v0.1" title
  - FPS counter showing current capture rate
  - Model status badges (YOLO: 60fps, VL: 8fps, Claude: connected/disconnected)
  - Settings gear icon

  BOTTOM BAR:
  - Quick action buttons: "Look", "Find Element", "Click At", "Type Text", "Capture Screenshot"
  - Text input for natural language commands ("click the Submit button", "fill in the email field with test@example.com")

  Tech: React + TypeScript, Tailwind CSS, dark theme (zinc-900 background, zinc-800 panels), WebSocket for live feed, shadcn/ui components.

  This connects to a Python FastAPI backend (not included) that runs the actual automation. The frontend just displays and controls.

## Assistant

I'll build this as a hi-fi interactive prototype. Since this is an original product ("SelfConnect Vision Agent"), I'll design from scratch with a custom aesthetic.

**Design direction:** Dark technical dashboard — zinc-900 base with subtle phosphor-green accent (oklch terminal vibe), JetBrains Mono for data + Inter for UI. Tactile, dense, instrumented — feels like a vision/robotics control panel.

_[tool: update_todos]_

_[tool: write_file]_

_[tool: done]_

_[tool: fork_verifier_agent]_

Built the SelfConnect Vision Agent dashboard — dark zinc theme with a phosphor-green accent, 3-column grid as specced.

**Left** — Live capture viewer streaming a simulated login screen with YOLO bounding boxes (green/blue/gray/yellow per class), each clickable to log element info. Frame counter, FPS, scanlines, corner reticles, pause/resume.

**Center** — VL model description panel (llava:13b style, with token + latency readout and tag chips), Action Queue with running/pending/cancel states, and Macro Recorder with live event table + REC pulse + elapsed timer.

**Right** — Target Windows list (hwnd / title / exe, attach buttons), Action Log (color-coded ✓/✗/◌ with timestamps, auto-scrolling), and Connection Status for SDK / YOLO / VL / Claude with heartbeat dots.

**Top bar** — Title + version, live FPS chip, three model badges, settings gear.
**Bottom bar** — Five quick actions (Look, Find Element, Click At, Type Text, Capture Screenshot) + natural-language command input that pushes to the queue.

Everything is wired: clicking a bbox logs it, recording captures inspect events into the macro table, queue auto-progresses, attaching a window updates state, and NL commands enqueue a planned action.

