# SelfConnect Process Timeline
<!-- selfconnect-runbook: {"status":"current","since":"2026-07-05","replacement":null,"entrypoint":false,"kind":"timeline"} -->

This file preserves why older runbooks exist while keeping the current operating
path easy to find. Do not delete older runbooks just because a better procedure
exists; mark them historical, explain what replaced them, and keep the evidence.

## Current Operating Model

As of 2026-07-05, SelfConnect terminal-agent work starts here:

1. `first_wake_selfconnect.md`
2. `agent_launch_registry.md`
3. Target-specific runbook only when needed

The stable rule is:

```text
runbook first -> MemoryWeb freshness check -> doctor/mesh health -> verified launch -> guard/read -> mesh register -> send -> readback -> event closeout
```

MemoryWeb is useful for fresh/session-specific context, but low-relevance
results must not override the written runbook.

## Timeline

### Early Win32 Proofs

The first runbooks captured individual Win32 primitives:

- `capture_chrome_window.md`
- `inject_webview2_chat.md`
- `enter_claude_tui.md`
- `peer_approval_check.md`
- `cross_machine_mesh.md`

Keep these. They are narrow, practical references for specific adapter behavior.

### Legacy Claude Spawn Pattern

`spawn_claude_terminal.md` documents the older raw Windows Terminal spawn flow:

```text
wt.exe -> cmd /k -> claude -> raw WM_CHAR briefing
```

This was valuable historically, but it is not the first-wake entrypoint anymore.
On 2026-07-05, direct `wt.exe` parsing failed twice while launching Claude:

- `wt new-tab ... claude` failed because `claude` was not resolved inside the
  new terminal environment.
- `wt new-tab ... "& '...\claude.ps1'"` failed because Windows Terminal treated
  the PowerShell call expression as the executable name.

Replacement: `first_wake_selfconnect.md`, using `Start-Process powershell.exe`
and an explicit CLI path when needed.

### Agent Launch Registry

`agent_launch_registry.md` became the canonical per-target recipe table after
CLI flags changed underneath the process. Example: `codex --full-auto` became
stale and was replaced with `codex -a never`.

Keep this file current whenever a launch recipe changes. It is the best place
for target-specific flags, startup waits, and submit quirks.

### Mesh Bootstrap

`mesh_agent_bootstrap_and_replacement.md` remains active for multi-agent mesh
replacement work. It is not the first-wake entrypoint; use it after the basic
SelfConnect health and launch path are verified.

### Historical One-Offs

These files should be treated as historical evidence unless a newer runbook
explicitly points to them:

- `spawn_claude_terminal.md`
- `spawn-and-operate-local-agentpy-mesh-agent-agent-b.md`
- `submit-pending-input-in-claude-code-tui-via-wm-char-0x0d.md`

They are indexed in `PARKED.md`. Do not delete them. If they become misleading,
add a superseded note at the top and link to the current replacement.

## Cleanup Policy

Use this policy when a runbook is stale:

1. Do not delete the file.
2. Add a short `Superseded` note at the top.
3. Link to the replacement runbook.
4. Record the reason in this timeline.
5. Keep old commands visible so future agents understand what changed.

This keeps the working set clean without losing the project history.

## Machine-Readable Status

Add this HTML comment near the top of runbooks so agents can detect whether a
procedure is current, superseded, or historical:

```html
<!-- selfconnect-runbook: {"status":"current","since":"2026-07-05","replacement":null,"entrypoint":true,"kind":"procedure"} -->
```

Valid `status` values:

- `current`: preferred procedure.
- `superseded`: preserved but replaced by another runbook.
- `historical`: retained for evidence or one-off context.

Only one runbook should have `entrypoint:true` for SelfConnect first-wake work:
`first_wake_selfconnect.md`.
