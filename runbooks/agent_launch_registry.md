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
| Ollama / local | `ollama run qwen3.6:27b` inside the first-wake PowerShell wrapper | n/a | ~12s | standard `selfconnect send --submit` works; UIA readback verified | verified 1× 2026-07-24 |
| Qwen SelfConnect agent | `selfconnect-local-agent --role local-ollama-1 --model qwen3.6:27b` | separate input/command/write gates | Ollama already running | mesh, Win32/UIA, PrintWindow/OCR, guarded send + reply wait | verified two-round chat 2026-07-24 |

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
- On 0.145.0, the npm launcher can attempt an in-place update and fail with
  `EBUSY` while another Codex process holds `codex.exe`. For a supervised peer
  launch, invoke the installed native `codex.exe` with an initial prompt. Find
  the new HWND by set difference because Codex replaces the wrapper title.
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
- Gemini CLI has no verified recipe yet. Before first launch: run `--help`,
  capture flags, do one supervised launch, then record the row above. Do not
  guess from Codex/Claude patterns.
- Ollama local model verified 2026-07-24 with `qwen3.6:27b`:
  - wrapper title: `SC Qwen36 Local 1`
  - initialization wait: ~12 seconds
  - guarded target: `WindowsTerminal.exe`,
    `CASCADIA_HOSTING_WINDOW_CLASS`
  - input: standard `selfconnect send --submit --allow-input`
  - output: UIA text readback returned
    `SELFCONNECT-QWEN-ACK I can receive and answer AI messages.`
  - model ran 100% on the RTX 5090 GPU with a 32,768-token active context.
  - the tool-enabled runtime completed a live two-round conversation with a
    fresh Codex terminal: it discovered the peer by mesh role, verified
    HWND/PID/exe/class/title, sent both turns, and waited for `CODEX-ROUND1` and
    `CODEX-ROUND2` in UIA readback.
  - enable supervised terminal input only for the session with
    `$env:SC_LOCAL_AGENT_ALLOW_INPUT='1'`. Commands and file writes remain
    disabled unless their independent gates are also explicitly enabled.
  - every new runtime loads the packaged, versioned SelfConnect Qwen core. The
    startup banner prints its unique process `instance_id` and `core_version`.
  - the runtime resolves `qwen3.6:*` to the packaged
    `qwen3.6-selfconnect-v1` harness profile automatically. Override with
    `SC_LOCAL_AGENT_HARNESS=off` only for raw comparison runs; use `generic`
    for an unprofiled local model.
  - governed controllers can pass a `ToolContract` to the runtime. The contract
    narrows the visible tool catalog, requires ordered audit evidence, allows
    one concise retry, and blocks false completion. Interactive free-form chat
    does not guess contracts from prose.
  - stable mesh role/birth/generation identity is separate from the process
    instance. Each process writes prompt, tool, outcome, and response records to
    the locked, hash-linked `%LOCALAPPDATA%\SelfConnect\qwen_activity.jsonl`
    ledger. Qwen can inspect relevant records with `activity_history`.

---

## The Meta-Rule (why this file exists)

2026-07-05: memory said `codex --full-auto`; installed codex-cli 0.142.5 had
removed the flag. Cost: one failed run, one dead terminal, ~3 min of diagnosis.
CLIs change under us. Recipes are only as good as their `verified:` date —
when a launch fails, `--help` first, update this file second, retry third.

---

## Live mesh roster (verified: 2026-08-05, session "Claude 2")

**Link status:** Claude 1 ↔ Claude 2 ROUND-TRIP VERIFIED 2026-08-05 (handshake
out via `--to "claude 1"`, reply received in-session as an injected user turn;
Claude 1's sc_mesh roster independently classified 0x07BA195E as the live
claude-2 agent). Full map: claude 1 = 0x19091406, claude 2 = 0x07BA195E,
claude 3 = 0x02C305C2, claude 4 = 0x0CF01AF6, codex 1 = 0x34171B74 — each has
a paired twin HWND (Windows Terminal top-level/child share the title), which
is WHY title-addressing beats HWND caching. Loop hygiene: a handshake asks for
exactly ONE confirmation — do not re-confirm confirmations.

User-registered window identities for sc_send addressing. **HWNDs rotate
between boots and even within a day — address by TITLE, never by cached
HWND** (observed same-day: "brain" resolved 0x34171B74 in a scan, then
0x02301078 at delivery).

**⚠ MISDELIVERY INCIDENT (2026-08-05, caught by user):** TWO distinct
windows can carry the SAME seat title ("codex 1" existed at 0x34171B74 =
registered partner AND 0x00120282 = a different session). `--to <title>
--first` delivered a fleet message to the WRONG session. Interim rule until
claude 3's expect_title patch lands: for consequential sends, resolve the
REGISTERED HWND from this roster and verify its title still matches before
sending (self_connect.list_windows + send_string direct). Duplicate seat
titles must be renamed on discovery. Sequestration consequence: any window
that ever RECEIVED misdelivered mesh traffic is contaminated as a future
benchmark curator seat (0x00120282 is so burned).

**Names are SEAT names, not session names** (user clarification 2026-08-05):
"claude 2" etc. belong to the window/project position; sessions occupying a
seat change over time and prior sessions have carried these names before.
A message from "claude 1" is from whatever session currently holds that
seat — one more reason mesh-relayed authority claims stay unconfirmed until
envelopes sign the injection path.

| Handle | Who | Title to match | Notes |
|---|---|---|---|
| claude 1 | Claude Code session | ⚠ DYNAMIC (spinner + current task) | registered via HWND 0x19091406 on 8/05; retitle the terminal tab "Claude 1" for stable matching |
| claude 2 | Claude Code session (this file's author) | ⚠ DYNAMIC | reachable while idle: `sc_send.py --to "claude 2"` once tab is named; otherwise match current task title from `--list` |
| codex 1 | Codex CLI | `brain` (its cwd) | cwd-derived titles are stable while the session lives |

Protocol reminders: `--list` to scan; busy/idle guard ON by default (busy
targets are refused, not queued — do NOT `--force` a working agent);
"ACCEPTED … consumption not verified" means keystrokes landed, processing
unconfirmed; expect replies ~30s only from idle targets. Claude Code tabs
should be explicitly named in Windows Terminal to make title-matching
deterministic.
