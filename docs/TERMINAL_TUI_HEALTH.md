# Terminal TUI Health: Flicker, Scroll, Selection, and Copy Lockout

## User-visible issue

An interactive agent terminal can remain responsive while temporarily refusing
mouse-wheel scrolling, disrupting text highlighting, and preventing reliable
copying. The symptoms often disappear together when the agent finishes a turn,
which can look like an intermittent Windows Terminal freeze.

This is a terminal-usability failure, not necessarily a process or window hang.
An alternate-screen TUI can own the viewport while an active render loop
repeatedly replaces terminal text. Those redraws can consume scroll behavior and
invalidate an in-progress selection even though UI Automation and `PrintWindow`
still work.

## Capture proof

Discover and guard the exact target first, then run:

```powershell
selfconnect doctor --json `
  --terminal-hwnd <HWND> `
  --terminal-seconds 5 `
  --terminal-interval 0.5 `
  --terminal-log proofs/terminal-health.jsonl `
  --capture-on-risk
```

The doctor samples UIA text, stores timestamps, sizes, and SHA-256 fingerprints
without storing terminal text in the JSONL log, and optionally captures a
screenshot. A result of `tui_redraw_risk` means:

- UIA remained readable;
- an active-agent TUI marker was present;
- the rendered terminal buffer changed repeatedly; and
- scrolling and selection should not be treated as reliably usable.

`stable_or_idle` does not prove that mouse input works. It means this bounded
probe did not observe the specific repeated active-TUI redraw condition.

## Codex remediation

Start Codex without the alternate screen:

```powershell
codex --no-alt-screen
```

For a persistent per-user setting, add:

```toml
[tui]
alternate_screen = "never"
raw_output_mode = true
```

to `%USERPROFILE%\.codex\config.toml`.

Launch wrappers and SelfConnect runbooks should still pass
`--no-alt-screen` explicitly. The flag protects launch paths that load a
different configuration directory or predate the configuration change.

The setting only applies when a new Codex process starts. An already-running
alternate-screen session must be exited or resumed in a newly launched process.

## Other agent TUIs

Do not automatically apply Codex flags to Claude, Gemini, or another terminal
application. First capture a doctor trace and identify that application's
supported inline or alternate-screen option. The observable failure can be the
same while the supported remediation differs.

## What SelfConnect should record

For any recurrence, preserve:

- HWND, PID, executable, window class, and title from the target guard;
- bounded terminal-health JSONL;
- screenshot when the doctor reports risk;
- agent name and launch command with secrets removed;
- whether scrolling, selection, and copying failed together; and
- whether usability returned at the same time the active render stopped.

This evidence separates an OS/window hang, failed UIA transport, and an active
but user-hostile TUI redraw loop.
