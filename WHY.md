# Decision Record

## 2026-07-15 - Select Terminal Input by Verified Window Class

### Decision

Use `WriteConsoleInputW` for `ConsoleWindowClass`, exact-HWND `WM_CHAR` for the
tested Windows Terminal CASCADIA surface, and make all paths return structured
acceptance evidence. Never convert a console-write failure into apparent
success by falling back to `PostMessageW`.

### Why

The Windows API contracts and the live defect are aligned: `PostMessageW`
returns before receiver processing. Its success proves queueing only. The
console input buffer is the native input boundary for `ConsoleWindowClass`.

Cross-console access temporarily changes caller console attachment. Success is
therefore conditional on both a complete target write and explicit restoration
to a snapshotted original console PID. Blind restoration to a parent process is
not sufficient evidence.

### Consequences

- Callers can distinguish `win32_console_input`,
  `postmessage_wm_char`, and `sendinput`.
- `delivery_verified` remains false for raw transport acceptance.
- Independent readback, ACK, or receiver effect is needed for delivery claims.
- Incompatible explicit transport requests fail closed.
