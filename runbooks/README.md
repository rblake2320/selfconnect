# SelfConnect Runbooks
<!-- selfconnect-runbook: {"status":"current","since":"2026-07-05","replacement":null,"entrypoint":false,"kind":"index"} -->

Structured, session-proven procedures for Win32 operations. Read the relevant runbook **before**
attempting any multi-step Win32 task. If you discover a new procedure after 3+ attempts or
corrective injections, write it down using `runbook_writer.py`.

For SelfConnect terminal launch/contact work, start with
[`first_wake_selfconnect.md`](first_wake_selfconnect.md). Older runbooks are kept
for timeline and evidence; do not delete them just because a newer procedure exists.
When a runbook is stale, mark it superseded and update
[`PROCESS_TIMELINE.md`](PROCESS_TIMELINE.md).

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

## Current Entrypoints

| Runbook | What |
|---------|------|
| [first_wake_selfconnect.md](first_wake_selfconnect.md) | First-wake SelfConnect checklist for launching and contacting terminal agents |
| [agent_launch_registry.md](agent_launch_registry.md) | Canonical per-target launch recipes, flags, waits, and submit quirks |
| [PROCESS_TIMELINE.md](PROCESS_TIMELINE.md) | Timeline of old vs current procedures and cleanup policy |
| [PARKED.md](PARKED.md) | Parked historical/superseded runbooks and their current replacements |

Read-only audit command:

```powershell
python selfconnect_first_wake_audit.py
```

It prints the required startup order and current pass/fail health signals without
launching agents, sending input, registering mesh roles, or editing files.

## Specialized Runbooks

| Runbook | What |
|---------|------|
| [capture_chrome_window.md](capture_chrome_window.md) | Capture Chrome/Edge/Electron window as PIL Image |
| [inject_webview2_chat.md](inject_webview2_chat.md) | Type text into WebView2-hosted chat (Gemini, VS Code) |
| [mesh_agent_bootstrap_and_replacement.md](mesh_agent_bootstrap_and_replacement.md) | Bootstrap fresh mesh agents and replace off-rails terminals |
| [enter_claude_tui.md](enter_claude_tui.md) | Submit text / press Enter in Claude Code TUI |
| [peer_approval_check.md](peer_approval_check.md) | Check peer terminal for approval prompt, inject y/n safely |
| [cross_machine_mesh.md](cross_machine_mesh.md) | Start multi-machine AI mesh (Windows ↔ Spark-1 ↔ Spark-2) |
| [fix_antigravity_gemini.md](fix_antigravity_gemini.md) | Restore Gemini chat when it silently fails to load in Antigravity |

## Historical References

These are retained for project history and failure analysis in
[`PARKED.md`](PARKED.md). Prefer the current entrypoints above unless a historical
file is explicitly needed.

| Runbook | Status |
|---------|--------|
| [spawn_claude_terminal.md](spawn_claude_terminal.md) | Superseded for first-wake launch by `first_wake_selfconnect.md`; useful historical raw-injection reference |
| [spawn-and-operate-local-agentpy-mesh-agent-agent-b.md](spawn-and-operate-local-agentpy-mesh-agent-agent-b.md) | Historical one-off agent procedure |
| [submit-pending-input-in-claude-code-tui-via-wm-char-0x0d.md](submit-pending-input-in-claude-code-tui-via-wm-char-0x0d.md) | Historical submit experiment; prefer `enter_claude_tui.md` |

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
