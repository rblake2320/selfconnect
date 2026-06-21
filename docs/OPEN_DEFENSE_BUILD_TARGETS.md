# Private defense build targets - not approved for public disclosure.

# Open Defense Build Targets

Last updated: 2026-06-20

This document identifies the open work that most strengthens SelfConnect's
defensive patent and competitive position. It is an engineering planning note,
not legal advice and not a public disclosure package.

## Architecture Pivot - June 20, 2026

The current build target is no longer "one more demo." The next proof is scale
and throughput discipline.

Authoritative planning docs:

- `docs/ARCHITECTURE_AUDIT_2026-06-20.md`
- `docs/ARCHITECTURE_CONSENSUS_2026-06-20.md`
- `docs/FABRIC_V2_BUILD_TARGETS.md`
- `docs/PATENT_DESIGN_AROUND_DEFENSE.md`
- `docs/LANCELOT_UAB_COMPETITIVE_DEFENSE.md`

Consensus sequence:

```text
File now on what is already proven.
Benchmark now against the current transport.
Then build Fabric V2 and prove the delta.
```

Treat `20-agent` as a stress profile, not a literal product requirement. The
actual requirement is stable N-agent operation beyond the current live mesh size
without wrong-window writes, route confusion, replay acceptance, unbounded queue
growth, or evidence loss.

## Current Open Mesh Lanes

Current registry snapshot:

| Role | Profile | Current task | Defense use |
| --- | --- | --- | --- |
| `codex-1` | explore | patent design-around defense complete | Coordinate next proof and keep branch artifacts clean |
| `claude-1` | governed | enterprise assurance probes / TPM attestation | Best lane for TPM attestation, service SID, job object, ETW |
| `rmc-1` | explore | role migration / continuation coordination | Best lane for migration, birth/generation, mesh continuity proofs |
| `B` | governed | governed lease gate complete | Best lane for runtime SID lookup and governed lease integration |
| `gemini-1` | explore | design review follow-up complete | Best lane for design-around review and alternate route critique |
| `codex-2` | explore | closing | No new task unless respawned with new birth ID |

Do not assume every terminal on screen is part of the mesh. Use `birth_id`, not
visual title alone.

## Highest-Value Defense To Build Next

### 0. Fabric V2 Host Service / IOCP Router

Current state:

- Frame/mailbox layer: DONE in `sc_fabric_v2.py`.
- Sign-once/MAC-many frames: DONE.
- Replay/deadline rejection: DONE.
- Bounded mailbox backpressure: DONE.
- Real Windows named-pipe ACK selftest: DONE.
- Benchmark path `--transport fabric_v2_frame_mailbox`: DONE.

Still open:

- production IOCP host service;
- per-user session router;
- long-lived named-pipe endpoint with bounded per-agent mailboxes;
- crash/restart recovery in the host;
- service-mode integration.

Tracker:

- https://github.com/rblake2320/selfconnect/issues/7

Why it matters:

- The frame layer proves the governed session semantics.
- The IOCP host proves the high-rate operational data plane.

Expected next artifacts:

- `sc_fabric_host.py` or enterprise host-service equivalent;
- IOCP/named-pipe host test suite;
- `docs/FABRIC_V2_HOST_SERVICE_PROOF.md`;
- V2 benchmark rerun against the host service.

### 1. Channel-Router Composition Proof

This is the top open proof because it joins the strongest claim families into
one runnable system:

1. terminal target -> `WM_CHAR`;
2. browser target -> UIA `ValuePattern.SetValue` / `InvokePattern`;
3. routing metadata -> named pipe or file registry, not visible terminal text;
4. governed mode -> lease gate required;
5. readback -> UIA/TextPattern or capture fallback;
6. local echo -> filtered with nonce/hash classifier;
7. result -> redacted PASS/FAIL/NA artifact.

Why it matters:

- Competitors can route around any one primitive.
- They have a harder time routing around a governed policy engine that chooses
  the correct native channel per target surface and still produces verified
  delivery evidence.

Expected artifacts:

- `experiments/win32_probe/channel_router_composition_probe.py`
- `experiments/win32_probe/results/channel_router_composition_PASS_redacted.json`
- `docs/CHANNEL_ROUTER_COMPOSITION_PROOF.md`
- unit tests for routing decisions that do not require a live desktop.

Status:

- Deterministic model proof: DONE on `test/win32-hardening-v1`.
- Redacted PASS artifact: DONE.
- Live throwaway/local PASS artifact: DONE.
- Unit tests: DONE.
- Strict public-browser no-keyboard rerun: still open as a separate stronger
  evidence step.

### 2. Strict No-MCP Browser Rerun

The current browser/Gmail handoff proof has the right control boundary: no MCP
in any control path. But one read fallback touched MCP once.

Build a strict rerun where MCP is not touched at all:

- browser open/navigation through normal browser UI or local launch;
- read via UIA/TextPattern or capture;
- fill via UIA ValuePattern;
- click via UIA InvokePattern or synthetic mouse;
- verify send/readback through UI state;
- emit redacted proof.

Why it matters:

- Removes a possible impeachment point.
- Strengthens the "MCP is optional adapter only" boundary.

### 3. Runtime OS SID Lookup For Governed Lease Gate

The current governed gate can validate role, `birth_id`, generation, HWND, and
`owner_sid_hash`, but runtime OS SID lookup is still the next hardening step.

Build:

- `OpenProcessToken`;
- `GetTokenInformation(TokenUser)`;
- `ConvertSidToStringSid`;
- hash comparison to lease `owner_sid_hash`;
- fail-closed behavior on `<unknown-sid>`.

Why it matters:

- Turns the lease gate from injectable/test-state proof into live OS-derived
  authority.
- Directly defends against API-key/localhost competitors.

### 4. Browser Multi-Tab / Stale-Tab Proof

Build a local two-tab or two-window browser proof:

- tab A has nonce A;
- tab B has nonce B;
- SelfConnect is asked to act on A;
- stale/wrong tab is denied;
- changed title/URL/control fingerprint is denied or marked uncertain.

Why it matters:

- Browser equivalent of stale HWND/generation safety.
- Prevents "smart invoke clicked the wrong matching element" attacks.

### 5. Echo-Filter + Signed Receipt Composition

Combine existing echo filtering with signed evidence:

- sent hash;
- observed delta hash;
- classification;
- signer identity;
- target facts;
- lease tuple;
- timestamp/latency;
- optional TPM/CNG or Ed25519 signature.

Why it matters:

- Converts readback from "we saw text" into a defensible delivery receipt.
- Directly protects the "verified delivery / ACK" family.

## Enterprise Assurance Builds

### 6. TPM Platform Attestation

Current state:

- TPM/CNG signing proof exists.
- Platform attestation through `NCryptCreateClaim` is still pending because the
  descriptor call shape returned `E_INVALIDARG`.

Build:

- doc-grounded `NCryptBufferDesc`;
- nonce buffer;
- PCR selection mask where supported;
- verifier stub;
- honest PASS/NA result.

### 7. Job Object Runtime Adapter

Current state:

- Job Object proof reportedly passed in the experiment lane.

Build:

- package-safe optional adapter;
- throwaway child process;
- memory/process limit;
- kill-on-close;
- test cleanup;
- result surfaced in `selfconnect doctor`.

### 8. ETW Audit Schema

Current state:

- ETW smoke probe exists.

Build:

- event schema for send, read, deny, approval, migration, lease check, protected
  checkpoint, and echo classification;
- optional provider registration;
- redacted event sample;
- keep hash-chain/signatures for tamper evidence.

### 9. Service SID Daemon Proof

Current state:

- service SID helper/probe docs exist.

Build:

- minimal service or service-wrapper proof;
- service SID enabled;
- named-pipe endpoint ACLed to the service SID;
- unprivileged UI/terminal client calls service;
- proof that service identity is distinct from the user.

## Broader Continuation Species

### 10. Cross-Platform Species Spec

Do not wait to implement every platform before describing the species for
counsel:

- Windows: HWND/SID/UIA/named pipe.
- Linux: Unix socket peer credentials, X11/Wayland target facts, AT-SPI
  read/write, cgroups/namespaces.
- macOS: AXUIElement, audit token, app bundle identity, TCC permissions,
  sandbox profiles.

Why it matters:

- Prevents competitors from avoiding by leaving Windows while preserving the
  same OS-native local peer-mesh pattern.

## Suggested Assignment

| Target | Best owner | Reason |
| --- | --- | --- |
| Channel-router composition proof | `codex-1` or `rmc-1` | Needs repo integration and mesh discipline |
| Strict no-MCP browser rerun | `codex-1` | Needs live Windows browser/UIA |
| Runtime OS SID lookup | `B` | Builds directly on governed lease gate work |
| TPM attestation | `claude-1` | Already in enterprise assurance lane |
| Job object adapter | `claude-1` | Enterprise containment lane |
| Gemini design-around critique | `gemini-1` | Good for alternate routes and counterarguments |

## Not Worth Building As A Defense Right Now

- CAPTCHA bypass: do not build or claim it. Build protected-checkpoint detection
  and pause instead.
- Broad public-site automation parity: not the current defended lane.
- Generic desktop-control breadth: UAB/Lancelot already stands in that space.
- MCP-first control: keep MCP as optional adapter, not the invention.
- Full service-mode productization before the core composition proof.

## Immediate Next Commandable Task

Task name:

`channel_router_composition_proof`

Success condition:

A redacted proof artifact showing one policy engine selecting different native
channels for terminal and browser targets, with metadata off visible text,
lease-gated governed mode, and echo-filtered readback.

This is the most patent-defensive thing open right now.

## Disclosure Warning

This file is private defensive evidence. Do not publish, quote in marketing, or
place into a public repository before patent counsel reviews it.
