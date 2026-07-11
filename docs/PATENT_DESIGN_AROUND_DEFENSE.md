# Private patent design-around defense - not approved for public disclosure.

# SelfConnect Design-Around Defense

Last updated: 2026-06-17

This is an engineering claim-strategy note for patent counsel. It is not legal
advice, not a novelty opinion, and not a public disclosure package.

## Legal Drafting Constraint

The design-around strategy should be built around enough disclosed
representative embodiments to support broad claim language. The filing should
not only describe the exact implementation that works today.

Official USPTO/MPEP source pointers:

- USPTO patent application overview:
  https://www.uspto.gov/patents/basics/apply
- USPTO provisional application page:
  https://www.uspto.gov/patents/basics/apply/provisional-application
- MPEP 2163, written description:
  https://www.uspto.gov/web/offices/pac/mpep/s2163.html
- MPEP 2164, enablement:
  https://www.uspto.gov/web/offices/pac/mpep/s2164.html
- MPEP 608, disclosure:
  https://www.uspto.gov/web/offices/pac/mpep/s608.html

Drafting implication: for each SelfConnect claim family, disclose the broad
genus plus multiple concrete species. The current Windows implementation is one
species, not the whole invention.

## Portfolio Posture

Think like a large portfolio team:

1. Assume competitors will copy the value and change nouns.
2. Claim the invariant chain, not only the first implementation.
3. File around the stack in layers:
   - authority;
   - target binding;
   - channel routing;
   - readback and delivery evidence;
   - echo/false-positive suppression;
   - migration identity;
   - governance profile;
   - audit/containment.
4. Keep narrow proof-backed claims for fast allowance and broader continuation
   material for later pressure.

The strongest SelfConnect invariant is:

> A local AI peer mesh where an agent action is authorized by local operating
> system identity and live target facts, delivered through the target surface's
> native channel, verified by independent readback, and classified to avoid
> mistaking the sender's own echo for receiver output.

## Design-Around Map

| Competitor workaround | What they would change | Counter-claim / embodiment to disclose |
| --- | --- | --- |
| Avoid Windows named pipes | Use Unix domain sockets, loopback mTLS, local RPC, COM, ALPC, gRPC, memory-mapped files, or brokered IPC | Claim OS-authenticated local endpoint identity and lease authority independent of IPC transport. Windows species: named pipe + `ImpersonateNamedPipeClient`; other species: Unix peer credentials, broker token, COM security context, service identity, mTLS cert bound to local process identity |
| Avoid SID wording | Use process token, service account, AppContainer SID, package identity, certificate subject, TPM key, Windows Hello, passkey, or hardware key | Claim an authority value derived from local OS or hardware-backed identity and bound to a role lease. Species: SID hash, token user, service SID, package identity, TPM/CNG key claim, hardware key signature |
| Avoid `HWND` | Use UIA element runtime IDs, browser tab IDs, automation IDs, WebView HWND child, process handle, accessibility tree root, window station/session, document URL/title fingerprint | Claim live target-fact binding using one or more local target identifiers: window handle, process ID, executable path, class, title, session, accessibility element ID, control fingerprint, document URL, provider identity |
| Avoid target guard by using element names | Search for a button/name and invoke it directly | Claim fail-closed pre-action verification against expected live target facts before any visible-surface action, not merely element discovery |
| Avoid `WM_CHAR` | Use `SendInput`, paste/clipboard, IME, terminal pseudo-console stream, UIA ValuePattern, direct console API, WebView message, AppleScript, accessibility action | Claim target-specific native write-channel selection for AI peer communication. `WM_CHAR` is one terminal species, not the genus |
| Avoid UIA readback | Use OCR, screenshot diff, DOM read, accessibility tree polling, console buffer read, WebView APIs, OS capture, clipboard readback | Claim independent receiver-surface readback by structured, visual, or surface-specific observer. UIA TextPattern/TextChanged is the Windows structured species |
| Avoid TextChanged events | Use polling, DOM mutation observer, terminal scrollback diff, screen-diff, event log, accessibility focus/value event | Claim push or pull observation of the receiver surface and record the method used. Do not require push events in the broadest claim |
| Avoid echo problem by hiding sender text | Use hidden metadata, zero-width markers, side-channel ACKs, or no-echo input controls | Claim false-positive suppression between sent frame and observed receiver output, including nonce/hash/sequence/time-window classification, side-channel ACK reconciliation, or explicit no-echo proof |
| Avoid nonce/hash wording | Use sequence IDs, vector clocks, MACs, transcript hashes, Merkle roots, bloom filters, signed receipts, HMACs | Claim a sent-frame evidence value compared against observed output or receipt. Species: nonce, hash, HMAC, signature, sequence, timestamp, Merkle chain |
| Avoid lease generation | Use session UUID, epoch, vector clock, monotonic counter, Lamport clock, birth timestamp, terminal fingerprint | Claim role freshness data that changes on migration/restart and invalidates stale target authority |
| Avoid `birth_id` | Use machine ID, process start time, terminal fingerprint, window creation time, container ID, browser profile ID | Claim durable instance identity separate from human-readable role name. Role names can migrate; instance identity disambiguates live terminals |
| Avoid browser UIA by using CDP/extension | Drive browser through CDP, extension, WebDriver, JS injection, DOM automation | Claim unified governance over target-specific browser channels, plus a no-CDP/no-extension species for restricted environments |
| Avoid no-MCP claim | Wrap everything as MCP tools but still use local OS action underneath | Claim that MCP/API/HTTP is optional adapter only and not required for the local control path. Also claim the local OS actuation path regardless of calling interface |
| Avoid local-only | Put a cloud broker between agents | Claim air-gapped/local mode as one protected species and local OS-authority enforcement as a condition for governed mode, even when an optional remote coordinator exists |
| Avoid job object | Use container, VM, AppContainer, restricted token, Linux cgroups/namespaces/seccomp, macOS sandbox, Windows Defender Application Control | Claim OS-backed containment of agent action execution independent of the containment primitive |
| Avoid TPM | Use software Ed25519, X.509, YubiKey, HSM, Secure Enclave, Windows Hello | Claim cryptographic identity bound to role/action/target tuple with hardware-backed identity as a dependent embodiment |
| Avoid ETW | Use OpenTelemetry, syslog, Event Log, journald, custom hash-chain, SIEM collector, Windows WER | Claim audit event emission plus tamper-evident evidence chain, with ETW as the Windows enterprise visibility species |
| Avoid visible terminal | Use browser chat, web app, desktop app, mobile mirror, RDP session, WebView, IDE panel | Claim visible or user-observable agent surface, including terminal, browser, desktop app, embedded webview, RDP/virtual desktop, or accessibility-exposed control |
| Avoid Windows entirely | Implement on macOS/Linux | Claim OS-native local peer mesh broadly, then Windows-specific dependent claims. Species: Windows HWND/SID/UIA/named pipe; Linux X11/Wayland/AT-SPI/Unix credentials; macOS AXUIElement/TCC/audit token |

## Claim Families To File Around

### Family A: OS-Identity-Bound Role Lease

Broad claim target:

- A local control-plane lease for an AI role.
- Lease authority is derived from local OS or hardware-backed endpoint identity.
- Lease binds role name, durable instance ID, freshness counter, target surface
  identity, and endpoint identity proof.
- UI or fallback actions fail closed when the tuple is stale or mismatched.

Representative species:

- Windows named pipe + caller SID + `owner_sid_hash`.
- Windows service SID / package identity.
- TPM/CNG key signing an action tuple.
- Unix domain socket peer credentials.
- mTLS cert bound to local process/service identity.

Design-around pressure: a competitor cannot avoid by replacing named pipes with
another local IPC if the authority still binds local endpoint identity to an AI
role/action tuple.

### Family B: Live Target-Fact Guard

Broad claim target:

- Before an AI action reaches a desktop/browser/terminal surface, the system
  verifies expected live target facts.
- Verification includes at least one stable identity and at least one freshness
  or context fact.
- The system refuses to act on stale, unexpected, self, or wrong-class targets.

Representative species:

- HWND + PID + exe + class + title.
- UIA root element + process + automation ID + name/control type.
- Browser tab/document title + URL nonce + process + accessibility tree root.
- RDP/session/window-station fact.

Design-around pressure: "smart invoke by name" is not enough if it does not
prove the live target is the intended receiver.

### Family C: Adaptive Native Channel Router

Broad claim target:

- The same AI peer message/action is routed through different native write
  channels based on target surface model and policy.
- Channel choice is logged and governed by the same lease/target guard.

Representative species:

- Terminal/ConPTY: `WM_CHAR`.
- Browser/Chromium: UIA `ValuePattern.SetValue` + `InvokePattern`.
- Desktop controls: `WM_SETTEXT`, `BM_CLICK`, `WM_COMMAND`, UIA Invoke/Value.
- Metadata/control plane: named pipe or file registry, not visible terminal
  text.
- Fallbacks: clipboard/paste, synthetic mouse, structured app APIs when allowed.

Design-around pressure: changing from `WM_CHAR` to UIA, CDP, clipboard, or
another adapter does not avoid the router if the governed AI mesh still selects
native channels by target surface.

### Family D: Echo-Filtered Verified Readback

Broad claim target:

- The receiver surface is observed after send.
- The system distinguishes sender-originated echo from receiver-generated
  output.
- The result becomes a delivery/response classification record.

Representative species:

- UIA TextChanged delta + nonce/hash classifier.
- UIA TextPattern poll + sent hash comparison.
- OCR/screenshot diff + nonce/sequence.
- Console buffer delta.
- Side-channel ACK reconciled with visible-surface readback.
- Signed receipt over message hash and observed target facts.

Design-around pressure: a competitor cannot simply say "we read the output" if
they cannot prove the output is not their own injected frame.

### Family E: Browser Surface Without Browser-Control Dependency

Broad claim target:

- A browser-hosted AI/chat/email/web surface is controlled through local OS
  surface primitives rather than requiring a browser extension, debugging
  protocol, WebDriver, public API, or MCP control path.
- The browser species can still be governed by the same target guard, lease, and
  readback logic as terminal surfaces.

Representative species:

- UIA ValuePattern body fill.
- UIA InvokePattern reply/send.
- Synthetic mouse where keyboard does not reach the surface.
- UIA/TextPattern or accessibility readback.
- Protected checkpoint detection and pause.

Design-around pressure: CDP/extension-based systems may be broader app-control
bridges, but SelfConnect owns the no-CDP/no-extension governed browser-surface
handoff species and can also claim the policy composition above browser-channel
choice.

### Family F: Explore/Governed Mode Switching

Broad claim target:

- Same capability engine runs under different policy profiles.
- Explore mode accelerates testing while retaining right-target safety.
- Governed mode adds identity, lease, audit, containment, and consent checks.
- Capability behavior is not forked; policy wrapper changes enforcement.

Representative species:

- Registry profile: `explore` vs `governed`.
- Always-on target guard.
- Optional lease gate for governed sends/reads.
- Optional ETW, TPM, job object, service SID, approval gate.

Design-around pressure: competitors cannot dismiss controls as separate product
variants if the claim covers one engine with policy-selectable enforcement.

### Family G: Cross-Machine / Historical AI-to-AI Continuity

Broad claim target:

- Earlier hub-mediated AI-to-AI workflows show the broader problem space:
  troubleshooting, collaboration, conflict resolution, handoff, and learning.
- SelfConnect narrows the mechanism to OS-native local desktop surfaces and
  local authority.

Representative species:

- December 2025 Spark/Windows hub AI-to-AI milestones.
- January 2026 teacher-student/NLF/DMS evidence.
- May/June 2026 Win32 SelfConnect terminal/browser proof.

Design-around pressure: this helps show continuity of invention work while
keeping direct SelfConnect claims grounded in committed Win32 evidence.

## Competitor Playbook And Response

### They will say: "We do AI-to-AI desktop communication too."

Response:

- Do not fight on that broad sentence.
- Ask whether they bind receiver authority to OS identity, live target facts,
  freshness generation, and echo-filtered readback.
- SelfConnect claim: verified OS-surface peer communication, not generic
  desktop automation.

### They will say: "We use UIA/CDP/extension and support more apps."

Response:

- Breadth is their lane.
- SelfConnect's lane is right-target delivery, no-CDP browser species,
  identity-bound authorization, and false-positive suppression.

### They will say: "We have governance too."

Response:

- Application-layer governance can be valuable.
- SelfConnect's key distinction is below-agent enforcement: OS identity,
  target facts, local IPC, and optional containment/audit primitives.

### They will avoid your exact terms.

Response:

- Claims should avoid overfitting to `HWND`, `SID`, `WM_CHAR`, `UIA`, or
  `named pipe` alone.
- Use those as dependent claims and examples.
- The broad claims should cover identity-bound lease, target-fact guard,
  native-channel router, and echo-filtered readback.

## Provisional Filing Checklist

Include for each claim family:

- problem statement;
- broad mechanism;
- Windows concrete embodiment;
- at least two alternate embodiments;
- data structures;
- state machine;
- failure modes and fail-closed behavior;
- redacted proof artifacts;
- pseudocode;
- diagrams;
- claim boundary / non-claims.

Minimum diagrams counsel should get:

1. Terminal species: AI A -> target guard -> `WM_CHAR` -> terminal B -> UIA
   readback -> echo filter -> receipt.
2. Browser species: AI A -> target guard -> UIA Value/Invoke -> browser surface
   -> UIA readback -> receipt.
3. Governed lease species: caller -> named pipe -> OS impersonation -> role
   lease -> send/read gate.
4. Migration species: role name -> old birth/generation denied -> new
   birth/generation accepted.
5. Design-around species matrix: Windows, Linux, macOS; terminal, browser,
   desktop app; structured read, visual read, side-channel receipt.

## Immediate Evidence To Build

1. Strict browser rerun with no MCP touched at all.
2. Channel-router proof in one executable:
   - terminal target uses `WM_CHAR`;
   - browser target uses UIA Value/Invoke;
   - routing metadata uses pipe/file registry;
   - governed mode requires lease gate.
3. Runtime OS SID lookup:
   - `OpenProcessToken`;
   - `GetTokenInformation(TokenUser)`;
   - `ConvertSidToStringSid`;
   - hash and compare against lease.
4. Stale-tab/browser proof:
   - two tabs;
   - one expected nonce;
   - wrong tab denied;
   - changed title/URL denied or marked uncertain.
5. Echo-filter + signed receipt composition:
   - sent hash;
   - observed delta hash;
   - classification;
   - signer;
   - target facts;
   - lease tuple.

## Do Not Overclaim

Do not publicly claim:

- first AI-to-AI desktop communication;
- first desktop automation bridge;
- first UIA automation;
- first adaptive app-control cascade;
- universal browser automation;
- CAPTCHA bypass;
- production TPM attestation until `NCryptCreateClaim` is fixed and verified;
- production daemon/service mode until productized.

## Anchor Sentence

SelfConnect's protected lane is not app automation breadth. It is a local,
OS-native AI peer mesh that binds authority to live target facts and local
endpoint identity, routes actions through target-specific native channels, and
creates verified readback records that distinguish the sender's own echo from a
receiver's actual output.

## Disclosure Warning

This file is private defensive evidence. Do not publish, quote in marketing, or
place into a public repository before patent counsel reviews it.
