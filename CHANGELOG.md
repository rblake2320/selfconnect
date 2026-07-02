# Changelog

All notable changes to SelfConnect are documented here.

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
