# Parked SelfConnect Runbooks
<!-- selfconnect-runbook: {"status":"current","since":"2026-07-05","replacement":null,"entrypoint":false,"kind":"parked-index"} -->

Parked runbooks are preserved for history, failure analysis, and recovery of old
techniques. They are not the normal path for first-wake launch/contact work.

Use `first_wake_selfconnect.md` first. Use `agent_launch_registry.md` for current
target-specific launch commands. Only open parked runbooks when debugging older
behavior or tracing why the process changed.

## Parked Files

| Runbook | Parked Because | Current Replacement |
|---------|----------------|---------------------|
| [spawn_claude_terminal.md](spawn_claude_terminal.md) | Older raw Windows Terminal spawn flow; direct `wt.exe` parsing failed on 2026-07-05 | [first_wake_selfconnect.md](first_wake_selfconnect.md) |
| [spawn-and-operate-local-agentpy-mesh-agent-agent-b.md](spawn-and-operate-local-agentpy-mesh-agent-agent-b.md) | One-off local-agent Agent-B procedure with old `wt.exe` assumptions | [first_wake_selfconnect.md](first_wake_selfconnect.md), [agent_launch_registry.md](agent_launch_registry.md) |
| [submit-pending-input-in-claude-code-tui-via-wm-char-0x0d.md](submit-pending-input-in-claude-code-tui-via-wm-char-0x0d.md) | Historical submit experiment that conflicts with newer Claude submit guidance | [enter_claude_tui.md](enter_claude_tui.md) |

## Policy

- Do not delete parked files.
- Keep top-level warning text in each parked file.
- Keep `selfconnect-runbook` metadata accurate.
- If a parked procedure becomes current again, update this file, `README.md`,
  and `PROCESS_TIMELINE.md` in the same session.
