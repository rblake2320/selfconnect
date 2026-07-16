# SelfConnect SDK v0.12.0

<!-- SC-CLAIM:product.core_win32_bridge START -->
**Windows-native terminal actuation and observation primitives for AI-assisted
desktop workflows.** SelfConnect selects the input transport from a verified
window class, applies target guards, and exposes optional readback, mesh, and
governance adapters. The core package is a Windows SDK, not a compliance or
authorization product by itself.
<!-- SC-CLAIM:product.core_win32_bridge END -->

```python
from self_connect import list_windows, send_string, save_capture
```

<!-- SC-CLAIM:truth.readme_tagged_catalog START -->
Every externally positioned capability or historical exercise retained in this
README is enclosed in a stable `SC-CLAIM` block. `tools/release_gate.py` binds
those tags to `release/claims.json` and fails on malformed, duplicate,
unregistered, mismapped, or excerpt-hash-mismatched tags. This is explicit-tag
coverage only: the gate does not reliably identify claims in arbitrary
natural-language prose, so human review remains part of release review.
<!-- SC-CLAIM:truth.readme_tagged_catalog END -->

## What It Does

<!-- SC-CLAIM:terminal.os_native_actuation START -->
On the tested Windows Terminal CASCADIA surface, SelfConnect uses exact-HWND
`WM_CHAR`. A successful `PostMessageW` call establishes queue acceptance only,
not receiver processing or visible delivery. Independent readback, a receiver
ACK, or another receiver-side effect is required for a delivery claim. This
actuation step does not require MCP, HTTP, or a cloud API; optional surrounding
adapters may use those technologies.
<!-- SC-CLAIM:terminal.os_native_actuation END -->

<!-- SC-CLAIM:terminal.console_input_transport START -->
On the recorded isolated `ConsoleWindowClass` / `cmd.exe` proof, SelfConnect
used `WriteConsoleInputW` against `CONIN$`, restored the caller console, and
verified a target-process effect independently of the API return. This result
does not establish support for every console host, TUI, integrity boundary, or
Windows Terminal tab.
<!-- SC-CLAIM:terminal.console_input_transport END -->

For the complete current boundary, read
[`docs/SELFCONNECT_PRODUCT_BOUNDARIES.md`](docs/SELFCONNECT_PRODUCT_BOUNDARIES.md).

## Runbooks

<!-- SC-CLAIM:runbooks.manual_capture START -->
The repository includes operator-authored Win32 runbooks.
`runbook_writer.py` creates a structured runbook when a caller or operator
invokes it; its "3+ retries" rule is a documented trigger guideline, not an
automatic retry observer.
<!-- SC-CLAIM:runbooks.manual_capture END -->

## Installation

<!-- SC-CLAIM:package.installation_extras START -->
The package declares a small core plus optional extras for UIA, MCP, Windows
service support, Telegram, and the ClaudeGo dashboard. Installing an extra
makes its dependencies available; it does not prove that a deployment has
enabled or validated that adapter.
<!-- SC-CLAIM:package.installation_extras END -->

```bash
pip install selfconnect
pip install selfconnect[uia]
pip install selfconnect[mcp]
pip install selfconnect[service]
pip install selfconnect[telegram]
pip install selfconnect[claudego]
pip install selfconnect[full]
```

## Package Probes And Guarded Input

```bash
selfconnect doctor --json
selfconnect doctor --windows
selfconnect windows --query "Claude"
selfconnect guard --hwnd 0x123456 --expect-pid 1234 --expect-class CASCADIA_HOSTING_WINDOW_CLASS
selfconnect read --hwnd 0x123456
selfconnect capture --hwnd 0x123456 --path proof.png
```

<!-- SC-CLAIM:package.cli_target_guard START -->
The CLI send path requires explicit input authorization and either matching
target expectations or an explicit confirmation of the currently inspected
target. It requires a terminal class by default. The selected transport must
report complete acceptance, and raw transport results keep
`delivery_verified=false` until independent readback or ACK evidence exists.
<!-- SC-CLAIM:package.cli_target_guard END -->

```bash
selfconnect send --hwnd 0x123456 --text "hello" --submit --allow-input \
  --expect-pid 1234 --expect-class CASCADIA_HOSTING_WINDOW_CLASS
```

## MCP Server

<!-- SC-CLAIM:package.mcp_guarded_surface START -->
The optional MCP server exposes `doctor`, `list_windows`, `read_window`,
`capture_window`, `verify_target`, and `send_text`. `send_text` is disabled
unless `SELFCONNECT_MCP_ALLOW_INPUT=1`; when enabled, it still requires target
verification fields or explicit current-target confirmation and requires a
terminal class by default. `doctor` reports capability probes; a probe such as
`tpm_identity` or `named_pipe_impersonation` indicates detected local support,
not use by every core path or an external validation or authorization.
<!-- SC-CLAIM:package.mcp_guarded_surface END -->

```bash
pip install selfconnect[mcp]
selfconnect-mcp
```

## Product Profiles

<!-- SC-CLAIM:product.profile_boundaries START -->
The repository documents `explore`, `governed`, and
government/high-assurance configuration profiles. They express intended policy
postures around the same reusable capabilities. Merely selecting a profile
does not provision WORM storage, hardware keys, an authorization boundary, an
ATO, a DoD Impact Level authorization, or independent assessment evidence.
<!-- SC-CLAIM:product.profile_boundaries END -->

## Mesh Registry

```bash
selfconnect-mesh scan --query Claude
selfconnect-mesh register --role claude-1 --hwnd 0x123456 --profile governed --task "review"
selfconnect-mesh update --role claude-1 --status working --task "review"
selfconnect-mesh event --type task_assigned --role claude-1 --summary "review"
selfconnect-mesh events --role claude-1 --limit 20
selfconnect-mesh verify-events
```

<!-- SC-CLAIM:mesh.registry_hash_chain START -->
The mesh registry assigns per-terminal birth IDs and records role lifecycle and
manual events in a local append-only SHA-256 hash chain. Verification detects
retained-entry edits, inserted rows, broken links, and malformed records. Mesh
events also capture a bounded Git snapshot. The local chain is tamper-evident,
not tamper-resistant against deletion or truncation without an off-host or WORM
checkpoint.
<!-- SC-CLAIM:mesh.registry_hash_chain END -->

## Approval Experiments

<!-- SC-CLAIM:approval.telegram_governed_roundtrip START -->
`approval_partner.py` and `approval_telegram.py` are separate local
experiments. A governed end-to-end ApprovalRelay-to-Telegram-to-target-guard
roundtrip is not part of the release evidence set, so the README does not
position the pair as an integrated unattended approval control.
<!-- SC-CLAIM:approval.telegram_governed_roundtrip END -->

## ClaudeGo Dashboard

<!-- SC-CLAIM:dashboard.claudego_local START -->
The optional local ClaudeGo package serves a dashboard that lists discovered
agent windows and approval-prompt status, with optional tray and Windows toast
adapters. Its automated tests use mocked Win32 integrations; this README does
not claim a current live desktop deployment.
<!-- SC-CLAIM:dashboard.claudego_local END -->

```bash
python -m claudego
```

## Structured Framing

<!-- SC-CLAIM:framing.structured_messages START -->
The SDK implements STX/NUL/ETX structured frames with a JSON header, UUID
message ID, sequence, topic, payload length, and optional readback-based ACK.
An ACK result is reported only after the configured independent verification
step; raw input acceptance alone is not an ACK.
<!-- SC-CLAIM:framing.structured_messages END -->

```python
from self_connect import parse_frame, send_frame

result = send_frame(target_window, from_hwnd=my_hwnd, payload="hello", ack=True)
```

## Public API Surface

<!-- SC-CLAIM:package.public_api_surface START -->
The v0.12 package exports the Win32 window, capture, class-selected input,
framing, UIA/child-text, clipboard, watchdog, approval-relay, listener,
migration, and foreground `SendInput` helpers declared by `self_connect.py`.
The release smoke test verifies that the declared export list contains no
duplicate or undefined symbols. Availability and behavior remain subject to
the function-specific Windows and optional-dependency boundaries.
The complete names and signatures are in `self_connect.__all__` and the API
sections of [`CLAUDE.md`](CLAUDE.md).
<!-- SC-CLAIM:package.public_api_surface END -->

## Recorded Implementation Exercises

These are historical implementation/exercise records, not universal product,
performance, legal-novelty, compliance, or authorization conclusions.

<!-- SC-CLAIM:history.agent_spawn_exercise START -->
- **Agent spawn exercise:** tracked screenshots record a Windows console being
  opened, a CLI agent command being entered, and a handoff appearing in that
  session. This is an implemented-and-exercised record, not a guarantee for
  every CLI, account, policy, or host configuration.
<!-- SC-CLAIM:history.agent_spawn_exercise END -->

<!-- SC-CLAIM:history.background_input_exercise START -->
- **Background input exercise:** tracked before/after captures record input on
  the tested unfocused/minimized Windows terminal route. This does not establish
  arbitrary background application control.
<!-- SC-CLAIM:history.background_input_exercise END -->

<!-- SC-CLAIM:history.bidirectional_chat_exercise START -->
- **Bidirectional exercise:** tracked session captures record messages in both
  directions between two Claude CLI terminals. They establish the recorded
  exercise only, not a general delivery guarantee.
<!-- SC-CLAIM:history.bidirectional_chat_exercise END -->

<!-- SC-CLAIM:history.cross_vendor_mesh_exercise START -->
- **Cross-vendor exercise:** tracked captures record Claude, Codex, and Gemini
  CLI sessions participating in the historical Win32-terminal mesh. Model and
  provider connectivity were outside the measured local actuation boundary.
<!-- SC-CLAIM:history.cross_vendor_mesh_exercise END -->

<!-- SC-CLAIM:history.framing_exercise START -->
- **Framing exercise:** tracked captures record agents exchanging and testing
  the STX/NUL/ETX framing implementation while it was being developed. The
  evidence supports implementation and exercise, not exclusive authorship or a
  legal novelty conclusion.
<!-- SC-CLAIM:history.framing_exercise END -->

<!-- SC-CLAIM:history.printwindow_ack_exercise START -->
- **Readback ACK exercise:** the framing code and tests implement a
  fingerprint/readback verification step after transport acceptance. It is not
  a universal receiver ACK and remains dependent on readable receiver output.
<!-- SC-CLAIM:history.printwindow_ack_exercise END -->

<!-- SC-CLAIM:history.antigravity_exchange_exercise START -->
- **Antigravity exchange exercise:** a tracked capture and session log record a
  Claude-to-Antigravity/Gemini exchange using the local Windows UI path. The
  record does not establish the remote model identity independently, and model
  provider traffic was outside the measured SelfConnect actuation/readback leg.
<!-- SC-CLAIM:history.antigravity_exchange_exercise END -->

<!-- SC-CLAIM:browser.edge_local_fixture START -->
- **Owned browser fixture:** the recorded Microsoft Edge proof read and
  controlled an owned local page through UIA and stopped at a mock protected
  checkpoint. It does not establish public-site, cross-browser, CAPTCHA-bypass,
  passkey, upload/download, or anti-bot-evasion capability.
<!-- SC-CLAIM:browser.edge_local_fixture END -->

## Transport Distinction (Not A Patentability Conclusion)

<!-- SC-CLAIM:transport.engineering_distinction START -->
The tested core terminal actuation path differs from common app-layer
HTTP/WebSocket/JSON-RPC agent integrations: it uses Windows input primitives at
the local terminal boundary and does not require a network call for that
actuation step. The broader SelfConnect ecosystem can use MCP, relays, storage,
or networked governance adapters. This engineering distinction is not a
conclusion about the existence or absence of prior art, and it is not a
patentability conclusion.
<!-- SC-CLAIM:transport.engineering_distinction END -->

## Antigravity / Electron Adapter

<!-- SC-CLAIM:antigravity.controller_surface START -->
`antigravity_controller.py` implements `connect`, `chat`, and
`AntigravityMonitor` interfaces over the repository's UIA-based Electron
adapter. Its presence establishes an implementation surface; actual operation
depends on the target application's current accessibility tree and is not
claimed for every Electron or WebView2 application.
<!-- SC-CLAIM:antigravity.controller_surface END -->

```python
from antigravity_controller import connect, chat

session = connect()
response = chat(session, "What model are you?")
```

## License

Apache License 2.0 - see [`LICENSE`](LICENSE).
