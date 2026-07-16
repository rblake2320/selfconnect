# SelfConnect Proven vs Untested Capability Map

Last updated: 2026-07-15

This note keeps the claim boundary clean. SelfConnect has made a real shift,
but not every possible Win32 surface has been tested yet.

## What Shifted

The original SelfConnect path was two primitives:

- Write: `PostMessage(WM_CHAR)` into another window.
- Read: `PrintWindow()` capture of that window.

That proved OS-native AI-to-AI communication was possible, but it had reliability
gaps:

- A successful `PostMessageW` call could be mistaken for delivery even though a
  `ConsoleWindowClass` receiver never saw the characters.
- The sender could accidentally type into the wrong target window.
- The receiver readback could confuse local echo with a real peer response.
- Routing and migration state could ride through visible terminal text.
- The system had limited identity, audit, and containment evidence for governed
  deployments.

The recent work shifted SelfConnect from a two-primitive demo into a layered
mesh:

- Target-guarded sends: HWND/PID/exe/class/title are checked before input.
- Class-selected terminal writes: tested CASCADIA uses exact-HWND `WM_CHAR`;
  `ConsoleWindowClass` uses `WriteConsoleInputW` with caller restoration.
- Structured acceptance: every send names its transport and distinguishes API
  acceptance from verified receiver delivery.
- Mesh registry: roles, tasks, profiles, HWNDs, and migration state are tracked.
- Profiles: `explore` for fast local testing, `governed` for enterprise-style
  validation.
- UIA readback: structured text and `TextChanged` events are now proven on
  terminal surfaces.
- Echo filtering: injected local echo is separated from external output.
- Package/MCP surface: probes can be installed and tested on other machines.
- Enterprise probes: named pipes/DACLs, impersonation, TPM/CNG, ETW, service SID,
  and job-object sandboxing are tracked as optional governed layers.

The reason for the shift is not security theatre. It is reliability first:
right-window delivery, verified readback, false-positive suppression, resumable
mesh state, and then governance/audit/identity where the deployment needs it.

## Proven So Far

These have been tested in this branch or recorded as committed probes:

- Terminal-to-terminal injection with `WM_CHAR` on the tested CASCADIA surface.
- `ConsoleWindowClass` input through `WriteConsoleInputW`, live-verified through
  an independent target-process effect.
- Terminal/window capture with `PrintWindow`.
- Window enumeration and target verification.
- Notepad-style control/input experiments.
- UIA structured text readback on terminal surfaces.
- UIA `TextChanged` event proof on Windows Terminal / ConPTY.
- Echo filtering using nonce/hash/readback records.
- Mesh registry role tracking and `explore` / `governed` profiles.
- Pipe-authenticated role lease/generation proof:
  - named-pipe request bound to OS caller identity;
  - generation increments across migration;
  - stale generation/HWND UI fallback rejected.
- Channel-router composition model proof:
  - terminal surface routes to `WM_CHAR`;
  - browser surface routes to UIA Value/Invoke;
  - metadata route stays off visible terminal text;
  - governed mode requires a lease;
  - stale generation and wrong target class fail closed;
  - echo-filtered readback is included in the proof record.
- Channel-router composition live proof:
  - throwaway terminal leg passed with `TextChanged_event`;
  - isolated local browser leg passed with UIA-class input/readback;
  - no MCP control path was used;
  - stricter public-browser no-keyboard rerun remains untested.
- Package commands and MCP adapter surface.
- Local browser proof on Microsoft Edge:
  - isolated browser profile launched to `127.0.0.1`;
  - browser HWND/PID/exe/class/title target-guarded;
  - local page text read through UIA;
  - owned form filled through UIA;
  - submitted sentinel observed;
  - mock protected checkpoint detected and not bypassed.
- ETW provider smoke probe.
- Service SID helper/probe documentation.
- Named pipe + DACL + impersonation proof in experiments/enterprise lane, plus
  a pipe-authenticated role lease proof for future control-plane integration.
- TPM/CNG identity proof in experiments/enterprise lane, with full attestation
  still requiring more work.
- Job Object containment proof in experiments/enterprise lane.

## Not Yet Proven

These should not be claimed as working until tested:

- Browser control across real public sites.
- Browser control parity across Chrome, Edge, and Firefox.
- Browser page content readback through DOM or screenshot/OCR fallback.
- Browser form filling across real public sites.
- Multi-tab routing and stale-tab detection.
- Browser download/upload workflows.
- WebAuthn/passkey flows.
- CAPTCHA solving or CAPTCHA bypass.
- Anti-bot / abuse-detection evasion.
- WebView2-hosted app control beyond normal HWND/UIA capture paths.
- Cross-browser parity.
- Universal delivery semantics across every console host and TUI.
- PID-safe `WriteConsoleInputW` routing across multiple Windows Terminal tabs.

## Browser And CAPTCHA Boundary

Browser testing is a separate proof family. A browser has at least three layers:

- Native browser chrome: tabs, address bar, menus, download UI.
- Page accessibility/DOM content: form fields, buttons, page text.
- Protected or adversarial surfaces: CAPTCHA, anti-bot checks, fraud controls.

SelfConnect can be tested against the first two layers on owned, local, or
permissioned test pages. The correct goal is to prove that SelfConnect can open
a browser, navigate, read page state, fill forms, click controls, detect
blocked/protected states, and report PASS/FAIL/NA.

SelfConnect should not be positioned as a CAPTCHA bypass tool. For protected
flows, the safe governed behavior is:

- Detect the CAPTCHA/protected checkpoint.
- Stop automated action.
- Ask for human completion or use an official test/sandbox flow.
- Record that the automation paused at a human-verification boundary.

That still helps the product: detecting a boundary and refusing to fake a human
is useful for enterprise/government trust, and it keeps everyday exploration
honest.

## Recommended Next Browser Proofs

1. Harden browser smoke proof:
   - Repeat the local proof on Chrome and Firefox where available.
   - Add screenshot/OCR fallback classification.
   - Keep the existing Edge PASS artifact redacted.

2. Multi-tab proof:
   - Open two tabs with unique titles/nonces.
   - Prove SelfConnect routes to the intended tab/window.
   - Detect stale or changed tab state.

3. Protected-flow proof:
   - Use a local mock CAPTCHA page or official test fixture.
   - Prove SelfConnect detects the protected state and pauses.
   - Do not attempt bypass.

4. Runtime integration:
   - Add browser results to `selfconnect doctor`.
   - Keep browser control under `explore` unless a governed policy wrapper is
     explicitly enabled.
