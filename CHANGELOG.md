# Changelog

All notable changes to SelfConnect are documented here.

## [0.12.0] — 2026-07-02

### Added — Orchestration layer (additive; `self_connect.py` unchanged)

Turns the mesh from fire-and-forget into an evented, acknowledged, governed
request/response system. Closes every gap found in the July 2026 market review
(vs Claude Code Agent Teams, Gas Town, Google A2A, NATS). See
`docs/ORCHESTRATION.md`.

- **`sc_tasks.py`** — file-backed task board with A2A lifecycle states
  (`submitted / working / input-required / completed / failed / canceled /
  rejected`), validated transitions (fail closed), cross-process `FileLock`
  claiming (Agent Teams pattern — no double-claims), dependency gating,
  retry + dead-letter escalation, hash-chained `events.jsonl`, and
  `wait_for_state` (state waits replace blind sleeps).
- **`sc_transcript.py`** — lossless result reading via Claude Code session
  JSONL tailing (replaces PrintWindow/OCR/console scraping). Project-dir
  encoding, incremental offset tailing with partial-line tolerance,
  `wait_for_assistant_reply`.
- **`sc_hooks.py` / `sc_hook_emit.py`** — installs UserPromptSubmit / Notification
  / Stop hooks into a spawned agent's project-local settings (idempotent,
  preserves existing hooks). Emitter advances the task: ack→working,
  notification→input-required, stop→turn-ended. Always exits 0 (never blocks
  the agent's turn).
- **`sc_done.py`** — explicit completion verb (Gas Town `gt done` analog);
  completion is a signal the agent emits, not an inference from the screen.
- **`sc_envelope.py`** — HMAC-signed message envelopes with correlation IDs +
  signed agent cards (A2A pattern); constant-time verify, replay window,
  fail-closed card loading.
- **`sc_spawn.py`** — evented spawn wrapper: agent-status budget gate → optional
  git worktree → task create → briefing file → hook install → TUI readiness
  check → doorbell injection → ack wait → retry-once → dead-letter. Win32 is
  isolated behind `_sc()` for testability.
- **`sc_nats_bridge.py`** — optional signed-envelope transport over the existing
  NATS :4222 container (JetStream at-least-once, request/reply correlation).
  Import-guarded; injection stays the last hop, billing untouched.

### Fixed
- `sc_spawn._launch` (conhost path): `cd /d "..."` inside a list-form `Popen`
  gets its quotes backslash-escaped by `list2cmdline`, which cmd.exe rejects on
  paths with spaces — `claude` never launched. Now passes `cwd=` to `Popen`
  directly. Caught by the live desktop run.

### Tests
- 46 new unit tests (`test_sc_tasks`, `test_sc_transcript`, `test_sc_hooks`,
  `test_sc_envelope`, `test_sc_spawn`) — all Win32 faked, run on any platform.
- Full suite: 549 passed, 9 skipped (Win32-only), ruff clean.
- Cross-process integration proof: task driven submitted→working→input-required
  →completed via real subprocess CLI calls, hash chain intact across 6 writes.
- **LIVE E2E PASS (2026-07-02, real desktop)** — `_live_spawn_test.py`:
  `spawn_agent()` opened a real interactive Claude Code window (subscription
  path), doorbell-injected the briefing, agent's own UserPromptSubmit hook
  acked on attempt 1 (submitted→working), agent created + verified
  `SC_LIVE_PROOF.txt` and ran `sc_done.py` itself (working→completed), Stop
  hook recorded turn end, hash chain verified over all 6 events. Evidence:
  `.sc_live_test/` (task JSON, events.jsonl, briefing).

## [0.11.0] — 2026-07-01

### Added
- Console I/O fast path (ported + hardened from selfconnect-alt):
  `_write_console_input` (WriteConsoleInputW), `_read_console_output`
  (ReadConsoleOutputW), `_resolve_console_pid`, and public `read_console_fast()`.
  Injects an entire string in ONE syscall instead of one PostMessage(WM_CHAR)
  per character with a per-char `time.sleep`.
- `send_string(..., mode=...)` — `"console"`/`"auto"` opt-in fast path;
  `"postmessage"` (default) preserves exact pre-0.11.0 per-hwnd behavior.
- tests/test_console_io.py — 13 unit tests (mocked kernel32), all green.

### Performance (measured live on this machine, dedicated conhost target)
- send_string 500 chars: ~25,000 ms (PostMessage @0.05 default) → ~1.5 ms
  (console path) = ~17,000x. Cost is ~constant vs linear in length.
- Console path also delivers a real VK_RETURN, so it SUBMITS in stdin-reading
  TUIs (verified: `echo` ran and was read back via read_console_fast).

### Notes / limitations
- Added explicit 64-bit ctypes prototypes for the console handle APIs (the
  upstream fork relied on int defaults — potential handle truncation on Win64).
- Console path resolves the target by PID, not HWND. All Windows Terminal tabs
  share one WindowsTerminal.exe PID, so `_resolve_console_pid` cannot reliably
  distinguish tabs — hence `mode="console"` is OPT-IN and best for single-console
  targets (Codex/cmd with a dedicated conhost). The mesh default stays
  `"postmessage"` (per-hwnd, always correctly targeted).

## [0.10.5] — 2026-06-21

### Added
- sc_fabric_windows_svc.py: Windows SCM service wrapper (pywin32 ServiceFramework)
  — SelfConnectFabricV2 service, auto-start, install/remove/start/stop/query
- sc_fabric_service.py: install-service, remove-service, start-service, stop-service,
  query-service CLI subcommands via sc_fabric_windows_svc
- sc_fabric_v2.py: create_pipe_security_attributes() — named-pipe DACL hardened to
  owner SID + SYSTEM, deny-all fallback; pipe_security_summary() for evidence
- runbooks/fabric_v2_windows_service.md: Windows service install/operate/remove runbook

### Changed
- Evidence matrix: Service SID daemon posture promoted from "documented/probed"
  to "productized"; DACL hardening promoted from "pending" to "proven hardened"
- Evidence matrix: Fabric V2 service wrapper boundary updated (SCM gap now closed)

### External blockers (no change, not engineering)
- TPM PASS: requires dTPM or Azure Attestation embodiment
- MSI signing: requires Azure Artifact Signing or SignPath Foundation
- Gemini scale: oauth-personal EOL June 18 2026; use Vertex AI API key
