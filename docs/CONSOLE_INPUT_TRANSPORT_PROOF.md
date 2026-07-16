# Console Input Transport Proof

Verified: 2026-07-15

## Defect

A guarded send to a verified `ConsoleWindowClass` target returned success from
`PostMessageW` but produced no visible input. A second self-send reproduced the
same false positive. Native `AttachConsole` + `WriteConsoleInputW` placed input
records in the target console and Enter caused the receiver to execute them.

Microsoft documents that `PostMessageW` returns after placing a message in a
queue and before the destination processes it. Queue acceptance therefore
cannot be represented as application delivery:

- [PostMessageW](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-postmessagew)
- [WriteConsoleInput](https://learn.microsoft.com/windows/console/writeconsoleinput)
- [AttachConsole](https://learn.microsoft.com/windows/console/attachconsole)
- [GetConsoleProcessList](https://learn.microsoft.com/windows/console/getconsoleprocesslist)

## Production Rule

`send_string(mode="auto")` selects by the target class already verified by the
guard:

| Target | Selected transport | Success means |
| --- | --- | --- |
| `ConsoleWindowClass` | `WriteConsoleInputW` to `CONIN$` | every requested input record was written and the caller console was restored |
| tested `CASCADIA_HOSTING_WINDOW_CLASS` | exact-HWND `PostMessageW(WM_CHAR)` | every message was accepted for queueing; receiver delivery remains unverified |
| other classes | foreground `SendInput` compatibility route | input insertion API accepted the events; receiver delivery remains unverified |

A console-write failure does not fall back to `PostMessageW`. That fallback
would recreate the false positive.

## Live Proof

`experiments/win32_probe/console_input_transport_probe.py` is opt-in and creates
an isolated `cmd.exe` console unless an explicitly described external target is
provided. PASS requires all of:

1. HWND/PID/executable/class/title guard success.
2. Actual transport `win32_console_input`.
3. Exact input-record count.
4. Successful restoration of the caller console.
5. An independent receiver effect with exact expected content.

The target title, sentinel, command, temporary path, and console contents are
not recorded. The tracked redacted artifact contains only hashes and bounded
facts:

`experiments/win32_probe/results/console_input_transport_PASS_redacted.json`

## Boundary

The proof establishes the recorded `cmd.exe` / `ConsoleWindowClass` path. It
does not establish parity across every TUI, console host, Windows build,
integrity level, or Windows Terminal tab. API acceptance without independent
readback or receiver effect remains transport evidence, not delivery evidence.
