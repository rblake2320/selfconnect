# Private defensive invention record - not approved for public disclosure.

# Patent-Oriented Prior-Art Snapshot

Last updated: 2026-06-17

This is not a legal novelty search. It is a practical engineering snapshot to
avoid obvious public overlap and keep SelfConnect claims grounded.

Do not copy this file to a public repository, marketing material, demo deck, or
external disclosure package before legal review.

## Public Areas Found In Quick Scan

| Area | Public examples | What they appear to cover |
| --- | --- | --- |
| Windows MCP / ODR | Microsoft MCP on Windows docs | Registry/discovery of MCP agent connectors on Windows |
| Native desktop automation for agents | `agent-desktop`, `agent-ctrl`, UI Automata-style tools | Accessibility-tree observation and action over desktop apps |
| Lancelot/UAB | Project Lancelot UAB page and public UAB repository | Universal app bridge, desktop control cascade, MCP/CLI/HTTP interfaces, AI-to-AI desktop automation, flow learning, UIA/CDP/COM/raw-input paths |
| Windows named pipes | Microsoft named-pipe security docs | IPC and access control primitives |
| UI Automation security research | Akamai UIA abuse research | UIA as a powerful automation/attack surface |
| General screen/desktop agents | Screen/desktop automation demos and products | Computer-use automation, screenshots, OCR, accessibility trees |

## Differentiation To Preserve

The strongest SelfConnect lane is not generic desktop automation. It is:

- AI peer-to-peer communication over OS-native desktop surfaces;
- visible terminal/browser/app surfaces as a data plane;
- a separate OS-native control plane for routing authority;
- target-guarded HWND/PID/exe/class/title validation;
- UIA structured readback and echo filtering;
- named-pipe-authenticated role leases with monotonic generations;
- optional governed layers: impersonation, TPM, ETW, service SID, job objects;
- explicit protected-checkpoint detection rather than CAPTCHA bypass.

The pipe-role-lease proof is especially valuable because it joins three
previously separate ideas:

1. Windows named-pipe caller identity.
2. AI mesh role migration.
3. UI fallback authorization based on role generation and HWND freshness.

## Non-Claims

Do not claim:

- that no prior art exists;
- generic "AI controls desktop apps";
- generic "AI-to-AI desktop automation";
- generic adaptive desktop-control cascades;
- generic "MCP connector registry";
- generic named-pipe security;
- CAPTCHA bypass;
- unrestricted public-site browser automation.

## Source Pointers From Quick Scan

- Microsoft Learn, "MCP on Windows overview":
  https://learn.microsoft.com/en-us/windows/ai/mcp/overview
- Microsoft Learn, "Named Pipe Security and Access Rights":
  https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights
- GitHub, `lahfir/agent-desktop`:
  https://github.com/lahfir/agent-desktop
- Akamai, "The Dangers of Windows UI Automation":
  https://www.akamai.com/blog/security-research/windows-ui-automation-attack-technique-evades-edr
- UI Automata introduction:
  https://automata.visioncortex.org/blog/introducing-ui-automata/

## Session Finding: Browser UIA Control Channel - 2026-06-17

This is a condensed, repo-traveling snapshot. It is not the full process record.
The full process record remains session-local and should not be copied into the
repository without redaction/legal review. This section intentionally excludes
raw Gmail content, screenshots, local paths, usernames beyond generic role
labels, HWND/PID values, pipe names, secrets, and raw transcripts.

### Core Defensive Claim

Every control/actuation action in the browser handoff loop was OS-native. No
MCP server, Gmail API, CDP/WebDriver session, browser extension, or bearer-token
tool channel was used to control or send the browser reply. The browser reply
was sent by manipulating Chrome's UI Automation tree: `InvokePattern` for
Reply/Send and `ValuePattern.SetValue` for body fill. Authority came from OS
window facts and the lease-gated SelfConnect control model, not from a web API
or tool schema.

The defensible distinction from MCP/HTTP competitors is not "desktop
automation" in the abstract. It is a local OS-native agent mesh that routes
actions by target-surface model, verifies target identity at the HWND/PID/class
layer, reads back state through UIA/TextPattern or other native surfaces, and
can place governed lease checks over each action.

### Channel Map

The browser session found a meaningful channel boundary:

| Target surface | Observed write channel | Result |
| --- | --- | --- |
| Terminal / ConPTY | `WM_CHAR` / `send_string` | Works for peer terminal text |
| Chromium / Gmail | `WM_CHAR` | Did not drive the page surface |
| Chromium / Gmail | `SendInput` / synthetic keyboard | Did not reliably drive the page surface |
| Chromium / Gmail | UIA `ValuePattern.SetValue` | Worked for body fill |
| Chromium / Gmail | UIA `InvokePattern` | Worked for Reply/Send actions |
| Chromium / Gmail | Synthetic mouse | Worked where synthetic keyboard did not |

This supports adaptive channel selection by target surface:

- terminal surfaces use ConPTY/`WM_CHAR`;
- browser surfaces use UIA value/invoke patterns;
- governed policy can be applied above both through the same lease gate.

### MCP Boundary

MCP appeared once as a bootstrap read fallback during the receiving-side email
read. It clicked nothing, sent nothing, and controlled nothing. It was not in
any control or actuation path. The same email body was later reproducible
through SelfConnect's own UIA/TextPattern read path, and a strict-mode rerun can
avoid MCP entirely.

The defensible claim is therefore:

> No MCP was used in any control path.

The defensible claim is not:

> MCP was never touched.

That distinction should remain explicit in patent, technical, and demo notes.

### Defensive Claim Families

| Claim family | Evidence snapshot | MCP/HTTP contrast |
| --- | --- | --- |
| Browser control through OS accessibility | Gmail reply body filled with UIA `ValuePattern.SetValue`; Reply/Send invoked with UIA `InvokePattern` | No Gmail API, MCP send, CDP/WebDriver, browser extension, or tool schema |
| Readback as delivery receipt | Post-action UI state verified after send; thread/reply state observed | Confirmation came from browser/UI state, not server-side API response |
| Cross-agent observation via UIA TextPattern | Peer terminal/email content observed through UIA structured text paths | No direct model API or peer API required |
| Fail-closed target guard | Existing target guard rejects wrong PID, non-terminal where terminal required, and stale HWND assumptions | Authority tied to OS window facts, not arbitrary tool invocation |
| SID-bound named-pipe role leases | Lease gate accepts current tuple and denies stale HWND, wrong generation, and wrong owner SID; raw SID not logged | Routing authority is OS-authenticated, not bearer-token-only |
| Channel-by-target-model routing | Terminals use `WM_CHAR`; Chromium uses UIA Value/Invoke; synthetic keyboard not assumed universal | One policy engine can govern multiple native surface channels |
| Echo-filtered readback | Nonce/hash echo filter separates local sent echo from real peer output | Avoids false response attribution without a network protocol |

### Test Validation

The spawned agent reported a narrower environment with approximately 300
passing tests. The live Win32 node later measured a broader scope at 395 passed
and 9 skipped after additional optional test dependencies were present. Remaining
trust-layer failures/errors were attributed to the missing optional
post-quantum `[pq]` / `dilithium-py` dependency, not observed regressions in the
browser/UIA/mesh-lease work.

The current tracked branch also includes targeted validation for:

- mesh lease gates;
- mesh registry birth IDs;
- echo filtering;
- local browser proof;
- package/MCP adapter surfaces.

### Disclosure Warning

This file is private defensive evidence. Do not publish, quote in marketing, or
place into a public repository before patent counsel reviews it.
