# SelfConnect Runbooks

Structured, session-proven procedures for Win32 operations. Read the relevant runbook **before**
attempting any multi-step Win32 task. If you discover a new procedure after 3+ attempts or
corrective injections, write it down using `runbook_writer.py`.

---

## Runbook Format

Each runbook follows this structure:

```
# Runbook: [Title]
## What
One-line description of what this achieves.

## Prerequisites
- Required imports / packages
- Required window state (visible, foreground, etc.)

## Steps
1. Step description
   ```python
   # runnable code block
   ```

## Known Failures
- What doesn't work and why (so sessions don't rediscover)

## Verified
- Date last tested, session number
```

---

## Index

| Runbook | What |
|---------|------|
| [capture_chrome_window.md](capture_chrome_window.md) | Capture Chrome/Edge/Electron window as PIL Image |
| [inject_webview2_chat.md](inject_webview2_chat.md) | Type text into WebView2-hosted chat (Gemini, VS Code) |
| [spawn_claude_terminal.md](spawn_claude_terminal.md) | Open new Windows Terminal tab running Claude Code |
| [enter_claude_tui.md](enter_claude_tui.md) | Submit text / press Enter in Claude Code TUI |
| [peer_approval_check.md](peer_approval_check.md) | Check peer terminal for approval prompt, inject y/n safely |
| [cross_machine_mesh.md](cross_machine_mesh.md) | Start multi-machine AI mesh (Windows ↔ Spark-1 ↔ Spark-2) |
| [fix_antigravity_gemini.md](fix_antigravity_gemini.md) | Restore Gemini chat when it silently fails to load in Antigravity |

---

## Adding a New Runbook

```bash
python runbook_writer.py \
  --title "My Procedure" \
  --what "What it achieves" \
  --step "Step 1 description" \
  --step "Step 2 description" \
  --fail "Thing that doesn't work: reason" \
  --prereq "Pillow>=10.0.0" \
  --session 15
```

This creates `runbooks/my-procedure.md` with the standard format.
