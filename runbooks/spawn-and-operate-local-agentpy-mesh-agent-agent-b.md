# Runbook: Spawn and operate local_agent.py mesh agent (Agent-B)
<!-- selfconnect-runbook: {"status":"historical","since":"2026-07-05","replacement":"first_wake_selfconnect.md","entrypoint":false,"kind":"parked"} -->

> Parked historical reference as of 2026-07-05. Prefer
> `first_wake_selfconnect.md` for first-wake launch/contact work and
> `agent_launch_registry.md` for current target-specific launch recipes.
<!-- selfconnect-runbook: {"status":"historical","since":"2026-07-05","replacement":"first_wake_selfconnect.md","entrypoint":false,"kind":"one-off"} -->

> Historical reference as of 2026-07-05. Prefer `first_wake_selfconnect.md`
> for first-wake launch/contact work and `agent_launch_registry.md` for current
> target-specific launch recipes.

## What
Run a local Ollama model (qwen3.6:27b) as a full mesh peer with bash/file/window tools via Ollama HTTP API tool calling

## Prerequisites
- `self_connect` available on sys.path

## Steps
1. Run: python spawn_b.py — spawns Agent-B in separate terminal via wt.exe -w new, waits for agent-b> prompt, updates mesh_config.py AGENT_B_HWND
2. If B gets stuck mid-inference: find PID with psutil (process name python.exe, cmdline contains local_agent), kill it, re-inject: C:\Python312\python.exe local_agent.py\r into the still-open terminal window
3. Inject tasks via: send_string(b, 'task text\r', char_delay=0.02) — always end with \r. Keep tasks under 7 steps. Give HWNDs as decimals directly — hex conversion burns iterations.
4. If B needs reset between heavy tasks: send_string(b, '/reset\r') — clears context but ONLY processes after current Ollama inference returns (120s timeout)
5. MAX_ITER=10 in local_agent.py — complex multi-step tasks will hit this. Increase to 15 for tasks >5 steps or split task into smaller injections
6. local_agent.py runs in bash (Git Bash / Linux) via subprocess — use Linux syntax in bash_exec. PYTHONPATH env var does not work in cmd.exe; use sys.path.insert(0,.) instead
7. Agent-E (Observer) spawned via: python spawn_observer.py — Claude Code haiku in separate cmd.exe window, reads observer_briefing.md on start, logs to observer_logs/
8. To approve E's pending input: restore_window(e.hwnd) + send_keys('enter') — Claude Code TUI requires foreground Enter, PostMessage alone is insufficient

## Known Failures
- None documented yet

## Verified
- 2026-05-07, session 14
