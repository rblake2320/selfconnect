# SelfConnect Proven vs Untested Capability Map

Last updated: 2026-06-17

This note keeps the claim boundary clean. SelfConnect has made a real shift,
but not every possible Win32 surface has been tested yet.

## What Shifted

The original SelfConnect path was two primitives:

- Write: `PostMessage(WM_CHAR)` into another window.
- Read: `PrintWindow()` capture of that window.

That proved OS-native AI-to-AI communication was possible, but it had reliability
gaps:

- The sender could accidentally type into the wrong target window.
- The receiver readback could confuse local echo with a real peer response.
- Routing and migration state could ride through visible terminal text.
- The system had limited identity, audit, and containment evidence for governed
  deployments.

The recent work shifted SelfConnect from a two-primitive demo into a layered
mesh:

- Target-guarded sends: HWND/PID/exe/class/title are checked before input.
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

- Terminal-to-terminal injection with `WM_CHAR`.
- Terminal/window capture with `PrintWindow`.
- Window enumeration and target verification.
- Notepad-style control/input experiments.
- UIA structured text readback on terminal surfaces.
- UIA `TextChanged` event proof on Windows Terminal / ConPTY.
- Echo filtering using nonce/hash/readback records.
- Mesh registry role tracking and `explore` / `governed` profiles.
- Package commands and MCP adapter surface.
- ETW provider smoke probe.
- Service SID helper/probe documentation.
- Named pipe + DACL + impersonation proof in experiments/enterprise lane.
- TPM/CNG identity proof in experiments/enterprise lane, with full attestation
  still requiring more work.
- Job Object containment proof in experiments/enterprise lane.

## Not Yet Proven

These should not be claimed as working until tested:

- Browser control through Chrome, Edge, or Firefox as normal desktop apps.
- Browser page content readback through DOM, accessibility tree, UIA, or
  screenshot/OCR fallback.
- Browser form filling across real sites.
- Multi-tab routing and stale-tab detection.
- Browser download/upload workflows.
- WebAuthn/passkey flows.
- CAPTCHA solving or CAPTCHA bypass.
- Anti-bot / abuse-detection evasion.
- WebView2-hosted app control beyond normal HWND/UIA capture paths.
- Cross-browser parity.

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

1. Local browser smoke proof:
   - Launch Edge or Chrome to a local HTML test page.
   - Verify HWND/PID/class/title.
   - Read page text through UIA and capture fallback.
   - Fill a normal form and verify submitted output.

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
