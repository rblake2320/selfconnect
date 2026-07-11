# Agent Launch Registry — Canonical Per-Target Recipes
<!-- selfconnect-runbook: {"status":"current","since":"2026-07-05","replacement":null,"entrypoint":false,"kind":"recipe-registry"} -->

> **Purpose:** ONE verified recipe per AI CLI target. No re-deriving, no guessing, no
> stale flags. If a launch fails, the FIRST move is `<cli> --help` to re-verify the
> flag, then UPDATE THIS FILE — same session, before anything else.
>
> **Rule (from track-and-adopt):** a recipe enters this table only after a live
> verified launch + injection round-trip. `verified:` date and count are mandatory.
> 3 consecutive successes = LOCKED. Until then, treat as provisional.

---

## Relationship To First-Wake

`first_wake_selfconnect.md` is the mandatory first file and owns the wrapper-level
startup flow. This registry is subordinate: use it to choose target-specific CLI
commands, flags, waits, and submit quirks after the first-wake health checks.

When this file shows `cmd /k ...`, treat that as the target command to run inside
the verified wrapper unless the row explicitly says otherwise. The default wrapper
for first-wake launch/contact remains `Start-Process powershell.exe` from
`first_wake_selfconnect.md`, because it avoided the 2026-07-05 `wt.exe` parsing
failures.

## Quick Reference Table

| Target | Launch command | Approval bypass | Init wait | Enter/submit quirk | Status |
|--------|---------------|-----------------|-----------|--------------------|--------|
| Claude Code | `cmd /k claude` | pre-approved allowlist in settings | ~5s after window | `\r` via WM_CHAR does NOT submit — needs settle+separate `\r`, or SetForegroundWindow+SendInput for stubborn cases | LOCKED (many sessions) |
| Codex ≥0.142.5 | `cmd /k codex -a never` | `-a never` (NOT `--full-auto` — removed) | ~18s | standard two-step works | verified 1× 2026-07-05 |
| Codex (legacy <0.142) | `cmd /k codex --full-auto` | `--full-auto` | ~25s | triple-approval pattern if flag omitted | SUPERSEDED |
| Gemini CLI | — | — | — | — | NOT YET VERIFIED — do help-check first |
| Antigravity (Gemini WebView2) | already-running app | n/a | n/a | UIA + AccessibleObjectFromWindow first, then WM_CHAR — see `fix_antigravity_gemini.md` | LOCKED |
| Ollama / local | — | n/a | — | — | NOT YET VERIFIED |

---

## Historical/Fallback Universal Launch Procedure

Prefer `first_wake_selfconnect.md` for first-wake launch/contact work. The
procedure below is retained for historical context and fallback engineering when
debugging launch wrappers; it is not the first-wake entrypoint.

```python
import ctypes, subprocess, sys, time
sys.path.insert(0, r"C:\Users\techai\PKA testing\selfconnect")
from self_connect import list_windows, restore_window, save_capture, send_string

user32 = ctypes.windll.user32

# 1. SNAPSHOT — hwnd set-diff is how you find the new window
before = {w.hwnd for w in list_windows()}

# 2. SPAWN — own console (CREATE_NEW_CONSOLE), never a WT tab you might need to kill
proc = subprocess.Popen(
    ["cmd.exe", "/k", "title MY-AGENT && <LAUNCH COMMAND FROM TABLE>"],
    creationflags=subprocess.CREATE_NEW_CONSOLE,
)
# proc.pid is the ONLY safe kill target. NEVER taskkill a PID from
# GetWindowThreadProcessId — on Win11 that's the shared WindowsTerminal.exe
# and kills EVERY terminal (2026-07-03 incident).

# 3. FIND — poll up to 30s for a new Console/Cascadia window
new_win = None
for _ in range(30):
    time.sleep(1)
    for w in list_windows():
        if w.hwnd in before: continue
        cb = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(w.hwnd, cb, 512)
        if "CASCADIA" in cb.value.upper() or "Console" in cb.value:
            new_win = w; break
    if new_win: break

# 4. WAIT for init (per-target time from table), then SCREENSHOT to verify
#    the CLI actually started — this is what catches a bad flag immediately.
time.sleep(18)
save_capture(new_win.hwnd, path="proofs/launch_check.png")
# READ THE SCREENSHOT. "error: unexpected argument" = stale flag. Fix table.

# 5. INJECT — two-step protocol, always
send_string(new_win, message, char_delay=0.02)   # text only, NO \r
time.sleep(1)                                     # buffer settle
send_string(new_win, "\r", char_delay=0.02)      # Enter separately

# 6. VERIFY — screenshots at 5s / 15s / 30s. No response by 30s =
#    check for stuck approval prompt, then re-ring once.
```

---

## Per-Target Notes

### Claude Code
- Spawning NEW agents: use `sc_spawn.spawn_agent()` (v0.12.0+) — ack, hooks,
  dead-letter, budget gate. Raw injection is fallback only.
- Talking to ALREADY-RUNNING terminals: two-step protocol above.
- Enter quirk: PostMessage `\r` fills the box; in stubborn TUI states use
  `send_keys()` (SendInput, needs foreground). See `enter_claude_tui.md`.

### Codex (codex-cli ≥0.142.5 — verified 2026-07-05)
- `codex --full-auto` was REMOVED. Errors with "unexpected argument" and drops
  to bare cmd — your injection then lands in an empty shell.
- Correct: `codex -a never` (`--ask-for-approval never`). Other values:
  `untrusted`, `on-request` (`on-failure` deprecated).
- `-C <dir>` sets working root; `--search` enables web search.
- Init ~18s to TUI ready (model banner visible).
- First contact 2026-07-05: 385-char injection, replied in <30s, model gpt-5.5.

#### Codex's OWN feedback on being driven externally (asked live 2026-07-05,
#### gpt-5.5 answered after researching developers.openai.com/codex docs)

Codex's equivalent of this registry = official docs + config profiles:
- `~/.codex/config.toml` profiles (config-basic doc) — persistent launch settings,
  no flags needed. Codex suggested this agent-driver profile:
  ```toml
  # ~/.codex/agent-driver.config.toml
  sandbox_mode    = "workspace-write"
  approval_policy = "never"
  allow_login_shell = false
  [sandbox_workspace_write]
  network_access = false
  writable_roots = []
  ```
- `codex exec` — official NON-INTERACTIVE mode (developers.openai.com/codex/noninteractive).
  For fire-and-forget tasks this may beat TUI injection entirely.
- Codex SDK + MCP-server mode (`codex mcp-server`) — programmatic driving without
  keystrokes at all. Candidates for a future SelfConnect transport.

Approval policy guidance (from Codex):
- `never` for unattended; `on-request` ONLY if the orchestrator (approval_partner)
  reliably detects and answers prompts; `on-failure` deprecated — avoid.
- NEVER use `--dangerously-bypass-approvals-and-sandbox` outside a disposable VM.

Sandbox guidance (from Codex):
- `workspace-write` = practical default for coding. `read-only` for review-only
  agents. `danger-full-access` only on isolated runners.
- Extra write paths: `--add-dir` or `writable_roots` — not full access.
- Network is a SEPARATE gate: `[sandbox_workspace_write].network_access = true`
  or expect silent failures/approvals. (Confirmed live: our Codex said "shell
  network access is restricted" at first contact.)
- Windows: native elevated sandbox preferred, falls back unelevated under
  enterprise policy. Failures log to `CODEX_HOME/.sandbox/sandbox.log`.

Injection gotchas — from the RECEIVER's perspective (Codex describing what it
needs from us):
- Inject only when the TUI is idle and composer-focused. Never mid-shell-command,
  mid-approval, or while tool output is streaming.
- Codex prefers PASTE-whole-prompt + single submit over char-by-char streams.
  (Our WM_CHAR char stream at 0.02s/char worked fine live, but for long payloads
  consider clipboard-paste or the console fast path.)
- `--no-alt-screen` runs the TUI inline with normal scrollback — better for our
  screen-scrape/UIA readback. Worth adding to the standard launch line.

#### Codex sandbox can block ALL subprocess/file-write (observed live 2026-07-05)
A Codex TUI session may READ files but neither execute subprocesses NOR write
files when the Windows sandbox helper fails at bootstrap:
`orchestrator_helper_launch_failed ... os error 206 (filename or extension too
long)`, helper `codex-windows-sandbox-setup.exe`, log `~/.codex/.sandbox/`.
Such a session CANNOT run `sc_send.py` or append to a file outbox (both blocked).

**Working channel for a sandbox-crippled Codex = SCREEN READBACK.** Inbound
injection via WM_CHAR is UNAFFECTED by the sandbox; Codex replies as on-screen
text read via `get_text_uia(hwnd)`. That IS the bidirectional loop: inbound =
inject, outbound = UIA scrape. No file/subprocess needed. (Live 2026-07-05:
Codex composed a correct ACK line on-screen; we read it via UIA even though its
own file-write of that same line failed.)
Fixes to try: relaunch Codex from a SHORT cwd (long path likely triggers 206);
or a config profile that disables the sandbox helper. Reconcile before relying
on Codex to execute anything.

Outbound tooling for peers that CAN execute: `sc_send.py` — generic peer-send
CLI, title-substring targeting, enforces two-step protocol, idle-guards busy
peers, refuses ambiguous targets. `python sc_send.py --list` shows windows.

## N-Directional Mesh — `sc_mesh.py` (proven live 2026-07-05)

One controller, many peers. `roster` discovers + classifies every injectable
agent terminal (claude/codex, idle/busy); `broadcast` fan-outs one message to
all idle peers (staggered 2s so windows don't interleave); `send`/`read`/`relay`
handle point-to-point and cross-peer relay. All sends use the two-step protocol
and skip busy peers unless `--force`.

**Live proof:** single broadcast to two terminals → both ACKed on-screen,
DIFFERENT vendors: `MESH-ACK Codex` (gpt-5.5) + `MESH-ACK claude-sonnet-4-6`.
Controller + N peers across vendors from one fan-out = the "army" is real.

Discovery scaled: roster found 12 live agent terminals on this desktop in one
call. CAUTION: `broadcast` with no `--kind` hits ALL idle agents including real
project sessions — scope with `--kind` or `send` to avoid hijacking working
terminals and burning tokens fleet-wide.

### Gemini CLI / local models
- NO verified recipe yet. Before first launch: run `--help`, capture flags,
  do one supervised launch, then record the row above. Do not guess from
  Codex/Claude patterns.

---

## The Meta-Rule (why this file exists)

2026-07-05: memory said `codex --full-auto`; installed codex-cli 0.142.5 had
removed the flag. Cost: one failed run, one dead terminal, ~3 min of diagnosis.
CLIs change under us. Recipes are only as good as their `verified:` date —
when a launch fails, `--help` first, update this file second, retry third.
