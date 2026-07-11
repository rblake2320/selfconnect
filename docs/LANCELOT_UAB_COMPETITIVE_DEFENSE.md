# Private competitive defense record - not approved for public disclosure.

# SelfConnect vs. Lancelot/UAB

Last updated: 2026-06-17

This is a practical engineering and claim-boundary note, not legal advice and
not a novelty opinion. Keep it private until patent counsel reviews the claim
language.

## Public Source Pointers

Accessed 2026-06-17:

- Project Lancelot competitive matrix:
  https://projectlancelot.dev/competitive-matrix/
  - Wayback capture requested: https://web.archive.org/web/20260617224949/https://projectlancelot.dev/competitive-matrix/
- Project Lancelot UAB page:
  https://projectlancelot.dev/uab.html
  - Wayback capture requested: https://web.archive.org/web/20260617225053/https://projectlancelot.dev/uab.html
- Public UAB repository:
  https://github.com/myles1663/UAB

Do not copy full page contents, screenshots, or repository code into this repo.
Use source URLs, dated notes, and redacted local proof artifacts.

## What UAB Publicly Occupies

Do not fight UAB on generic desktop automation breadth. Its public pages and
repository position it as a universal desktop app bridge with:

- UIA, CDP, COM, framework hooks, browser extension, raw input, and screenshot
  verification paths;
- MCP tools, CLI, library, and HTTP server interfaces;
- app/framework discovery and a persisted app profile registry;
- element-name invocation and spatial/deep-query style UI scanning;
- flow learning/replay;
- AI-to-AI desktop communication examples;
- Lancelot governance marketing around risk tiers, trust ledger, receipt DAG,
  and operator identity.

That means SelfConnect should not claim:

- first AI-to-AI desktop communication;
- first UIA app control;
- first adaptive channel/cascade selection;
- first generic agent desktop bridge;
- broad "screenshots are obsolete" positioning;
- CAPTCHA bypass or anti-bot evasion.

## SelfConnect's Defensible Lane

SelfConnect is not a universal app bridge. The strongest lane is:

> SelfConnect is a governed OS-native AI peer mesh whose authority is bound to
> the operating system's own identity and window facts, with verified delivery
> and echo-filtered readback across target-specific desktop channels.

The difference is compositional. Individual primitives such as UIA, named
pipes, window messages, and screenshots are public. The defensible SelfConnect
claim is the chain:

1. target facts are verified before action: HWND, PID, exe, class, title;
2. mesh roles carry birth IDs and monotonic generations;
3. governed routing can require a named-pipe lease bound to caller SID;
4. UI fallback is denied when HWND, generation, birth ID, or owner SID hash is
   stale or mismatched;
5. readback uses UIA/TextPattern/TextChanged or fallback capture;
6. echo filtering separates local injected text from external peer output;
7. browser and terminal surfaces use different native write paths under one
   policy model.

## Counter-Matrix

| Dimension | UAB public position | SelfConnect position |
| --- | --- | --- |
| Primary product frame | Universal app bridge / desktop control runtime | OS-native AI peer mesh and communication substrate |
| Authority model | API key / localhost server surfaces, plus app-level permissions | OS caller identity via named-pipe impersonation and SID-bound lease checks |
| Target safety | PID/app/element invocation model | Fail-closed HWND/PID/exe/class/title guard before input |
| Migration identity | App registry / profile persistence | Role + birth_id + generation + HWND + owner_sid_hash tuple |
| Browser path | Chrome extension and CDP are priority lanes; UIA is fallback | Proven browser control through UIA Value/Invoke without CDP, extension, MCP send, or WebDriver |
| Terminal path | General desktop control | ConPTY/terminal-specific `WM_CHAR` channel with readback and echo filtering |
| AI-to-AI reliability | AI-to-AI desktop communication claimed publicly | Echo-filtered readback with nonce/hash records to avoid mistaking local echo for peer output |
| Control-plane separation | CLI/HTTP/MCP/library app-control interfaces | Visible terminal/browser data plane plus optional named-pipe/file control plane |
| Governed assurance | Lancelot governance layer marketed above UAB | Optional OS-layer gates: SID lease, DACL pipe, TPM/CNG, ETW, service SID, job object |
| Air-gapped posture | Localhost/local runtime positioned as local | No API/CDP/extension/MCP control path required for proven browser UIA handoff; suitable for stricter no-network/no-extension profiles |
| MCP dependency | MCP tools are a primary discovery/control interface | MCP adapter is optional packaging; proven terminal and browser actions do not require MCP |

## MCP Boundary

SelfConnect includes an MCP server so MCP-capable agents can call packaged
SelfConnect tools. That does not make MCP the invention and does not make MCP
necessary for the proven actions.

The proven action paths are:

- terminal send: `WM_CHAR` to a verified terminal target;
- terminal read: UIA TextPattern/TextChanged or capture fallback;
- browser send: UIA `ValuePattern.SetValue` and `InvokePattern`;
- mesh identity: registry role/birth/generation plus optional SID-bound lease;
- governed routing: named pipe / file control plane, not visible terminal text.

The MCP server is therefore an adapter around SelfConnect. It is useful for
installation, interoperability, and testing on other agents, but the defensible
claim is that SelfConnect can act without MCP, CDP, WebDriver, browser
extensions, public APIs, or network transport in the control path.

## High-Confidence Differentiators

### 1. SID-Bound Role Leases

UAB's public material describes API-key-protected local interfaces. That is an
application-layer secret. SelfConnect's governed lane uses Windows named-pipe
impersonation to bind a role lease to the caller's OS SID and compares only a
redacted `owner_sid_hash` in ordinary records.

Claim boundary:

- Do not claim invention of named-pipe security.
- Claim the use of OS-authenticated caller identity as a lease authority for an
  AI mesh role, tied to role, birth ID, generation, target HWND, and owner SID
  hash.

### 2. Fail-Closed Target Guard

UAB's public examples emphasize finding and invoking named elements. That is
good product ergonomics, but the security question is different: "Am I acting
on the exact window I meant to act on?"

SelfConnect checks the live target before input and fails closed on wrong PID,
wrong class, stale HWND assumptions, unexpected title/exe, or self-targeting
where prohibited.

Claim boundary:

- Do not claim generic element discovery.
- Claim pre-action live target verification for OS-surface-mediated AI peer
  messaging and browser/terminal control.

### 3. Echo-Filtered Readback

In desktop-surface AI-to-AI communication, the sender's own injected text can
appear in the receiving surface before the actual peer response. SelfConnect
has a reusable echo filter that records nonce/hash evidence and classifies
echo-only, external output, mixed output, no signal, or unknown.

Claim boundary:

- Do not claim generic UI text readback.
- Claim false-positive suppression for OS-surface AI-to-AI communication where
  sent frames and received frames share the same visible/UIA read surface.

### 4. No-CDP Browser UIA Proof

UAB's public browser lanes prioritize extension and CDP paths, with UIA as a
general fallback. SelfConnect has a private proof of browser action through UIA
`ValuePattern.SetValue` and `InvokePattern` without MCP send, Gmail API,
CDP/WebDriver, extension, or synthetic keystroke control.

Claim boundary:

- Do not claim universal browser automation or public-site parity.
- Claim adaptive channel routing where Chromium/browser surfaces are driven by
  UIA Value/Invoke when terminal-style `WM_CHAR` and synthetic keyboard paths
  do not reach the page surface.

## Anchor Sentence

SelfConnect is not a universal app bridge. It is a governed OS-native AI peer
mesh whose authority is bound to the operating system's own identity and window
facts, so control, proof, and audit can hold without API keys, MCP control
tools, CDP, browser extensions, or network transport.

## Immediate Evidence Priorities

1. Keep Lancelot/UAB as known public overlap in `CLAIM_EVIDENCE_MATRIX.md`.
2. Do a strict browser rerun with no MCP at all and a redacted PASS artifact.
3. Add a channel-router proof:
   - terminal target -> `WM_CHAR`;
   - browser target -> UIA Value/Invoke;
   - routing/metadata -> named pipe or file registry;
   - governed mode -> lease gate required.
4. Add runtime OS SID lookup to the governed lease gate so `owner_sid_hash` is
   derived from the current process token instead of injected test state.
5. Keep all language away from CAPTCHA bypass, unrestricted public-site control,
   or generic first-to-automate claims.

## Disclosure Warning

This file is private defensive evidence. Do not publish, quote in marketing, or
place into a public repository before patent counsel reviews it.
