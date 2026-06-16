# SelfConnect Win32 Capability Backlog

Date: 2026-06-16
Branch: `test/win32-hardening-v1`

This backlog preserves the next Win32 capability ideas after the first hardening
pass. It is an engineering and product-planning artifact, not legal advice.
Patent language should be reviewed before filing or public claim use.

## Current Proven Surface

SelfConnect is no longer only using `WM_CHAR` and `PrintWindow`.

Already implemented or proven across the SDK, enterprise branch, and Win32
probes:

- `WM_CHAR` / `PostMessage` terminal injection.
- `PrintWindow` capture.
- UIA structured text reads.
- UIA `TextChanged` event delivery.
- named pipe transport.
- named pipe DACL / `ImpersonateNamedPipeClient` proof.
- TPM/CNG signing proof.
- `WM_COPYDATA` structured transport proof on the enterprise side.
- clipboard read/write.
- window move/resize/minimize/maximize/restore.
- `SendInput` / `send_keys`.
- `BM_CLICK`, `WM_COMMAND`, `WM_SETTEXT`, and related control messages.
- capture-exclusion APIs.
- safe target guard for shippable CLI/MCP input:
  - live HWND verification.
  - expected PID/exe/class/title matching.
  - terminal-class requirement by default.
  - explicit non-terminal override.
- `MessageListener`.
- role migration and checkpointing.
- app-level egress gating plus WFP policy tooling.
- hash-chained audit ledger.
- composed chained channel:
  - UIA event / text delta.
  - message hash.
  - identity signature.
  - DACL named pipe.
  - receiver impersonation.
  - OS SID verification.

Approximate current coverage: 8 to 10 major Win32 capability families.

## Engineering Clarifications

- `WM_COPYDATA` is a useful structured local data plane, but it is not the same
  security boundary as a named pipe with a DACL and impersonation. Treat it as a
  structured message primitive unless paired with separate HWND/PID/process and
  identity checks.
- ETW is a visibility and observability layer, not a tamper-evident ledger by
  itself. Keep the hash chain or signatures for tamper evidence.
- TPM key usage and TPM attestation are different proof levels. Last night's
  proof established TPM-backed signing behavior; procurement-grade remote
  verification needs key attestation.
- Forward-looking Microsoft agent platform items should be verified against
  current official Microsoft documentation before product or patent language
  depends on them.

## Tier 1: Highest Leverage, Doable Today

### ETW Provider

What it gives SelfConnect:

- Emits agent actions, approval decisions, identity stamps, and policy events
  into the native Windows telemetry path.
- Lets SOC tooling, Event Viewer, Sentinel-style pipelines, or EDR tooling read
  SelfConnect events without a custom log parser.
- Complements the hash-chained ledger rather than replacing it.

Recommended proof:

- Register a SelfConnect ETW provider GUID.
- Emit events for send, read, approval, denial, identity, policy block, and
  migration.
- Verify events with Windows tooling and preserve event schema.

Current artifact:

- `experiments/win32_probe/etw_provider.py`
- Smoke result on 2026-06-16: `EventRegister`, `EventWriteString`, and
  `EventUnregister` returned `0`.

### TPM Key Attestation

What it gives SelfConnect:

- Upgrades from "this key was used through the TPM provider" to a
  remote-verifiable claim about key origin.
- Makes the hardware identity story stronger for enterprise and government
  review.

Recommended proof:

- Add an experiment using CNG attestation APIs such as `NCryptCreateClaim` with
  platform claims where supported.
- Store verifier output and failure modes.
- Keep Ed25519 software identity as the default SDK path.

### Job Object + Restricted Token Sandbox

What it gives SelfConnect:

- OS-level containment for spawned agent processes.
- CPU, memory, process-tree, and kill-on-close controls through Job Objects.
- Reduced token privileges for agent subprocesses.
- A better containment primitive than app-only guardrails.

Recommended proof:

- Spawn a throwaway process in a Job Object.
- Apply memory/process/time limits.
- Kill the tree by closing the job.
- Spawn a restricted-token child and verify stripped privileges.

### Windows Service + Service SID

What it gives SelfConnect:

- A daemon identity separate from the logged-in user.
- Survival across user logoff.
- Service Control Manager restart behavior.
- Per-object ACLs scoped to the service SID.

Recommended proof:

- Prototype a minimal SelfConnect service wrapper.
- Expose only a local named-pipe API to the user session.
- Keep UI/terminal agent code unprivileged.
- Do not require the whole app to run elevated.

Current artifacts:

- `experiments/win32_probe/service_sid_probe.py`
- `experiments/win32_probe/SERVICE_SID_DAEMON.md`
- Current proof is SCM/SID helper only; a real service payload still needs a
  native service executable, pywin32 wrapper, or experiment wrapper.

### Job Object Sandbox Result

Claude1 reported `job_sandbox.py` PASS on branch `experiment/win32-probe`,
commit `48c5d94`:

- Job Object `KILL_ON_JOB_CLOSE`.
- `ActiveProcesses=1` confirmed live.
- active process limit: `1`.
- memory limit: `256MB`.
- child process killed by the OS on `CloseHandle(job)`.
- Result: OS-enforced containment proof passed. This is the containment path
  that app-only ExecGuard / SCFH could not provide.

Next step is to pull or port that probe into this branch after reviewing the
exact implementation.

### TPM Attestation Result

Claude1 reported `tpm_attestation.py` NA on branch `experiment/win32-probe`,
commit `48c5d94`:

- `NCryptCreateClaim(NCRYPT_CLAIM_PLATFORM)` returned `E_INVALIDARG`.
- Current diagnosis: malformed call shape, not a hardware/TPM capability limit.
- Needs a doc-grounded `NCryptBufferDesc` with nonce and PCR mask.
- Keep TPM signing proof as valid; do not claim remote-verifiable platform
  attestation until the `NCryptCreateClaim` invocation is corrected.

Claude1 next-four status after commit `48c5d94`:

- `target_guard`: PASS.
- `tpm_attestation`: NA / diagnosed.
- `job_sandbox`: PASS.

### WinVerifyTrust Pre-Execution Check

What it gives SelfConnect:

- Authenticode validation before executing agent binaries or helper tools.
- A clear trust gate before write-before-execute workflows.

Recommended proof:

- Verify a signed Windows binary.
- Verify an unsigned local script/tool fails or returns "unsigned".
- Log result into the ledger and ETW backlog schema.

## Tier 2: Strong, Mostly Doable Today

### WM_COPYDATA Structured Data Plane

What it gives SelfConnect:

- Atomic structured messages for local windows.
- Better routing messages than char-by-char `WM_CHAR`.
- A possible lightweight sidecar control plane for peer announcements.

Guardrail:

- Do not treat sender identity as OS-verified unless independently validated.
- Preserve named pipes for identity-sensitive control messages.

Recommended proof:

- Add a 64-bit pointer round-trip test for `COPYDATASTRUCT.lpData`.
- Send a signed JSON routing update.
- Verify target HWND/PID/exe/class before processing.

### Windows Hello / Human Approval Gate

What it gives SelfConnect:

- A stronger human consent leg than terminal `y/n` prompts.
- Useful for high-risk actions, credential use, or policy changes.

Recommended proof:

- Identify the correct API surface for local Hello/WebAuthn-style approval.
- Keep this optional and off by default.
- Log challenge, result, and action hash.

### WFP Egress Enforcement

What it gives SelfConnect:

- OS-level network containment below application code.
- Per-agent or per-service network policy that the agent process cannot easily
  bypass.

Recommended proof:

- Convert the existing WFP policy tool into a repeatable experiment.
- Apply an allow/deny rule to a test process.
- Verify blocked egress and clean teardown.

### Native Toast Approval UI

What it gives SelfConnect:

- Local Windows approval prompts without relying only on terminals or Telegram.
- Better fit for a Windows package with identity.

Recommended proof:

- Add a toast notifier adapter.
- Route approval decisions back to the policy engine.
- Keep terminal and Telegram approval paths intact.

### Precise UIA Deltas and WebView2 Reach

What it gives SelfConnect:

- Cleaner read deltas than full-text diffing.
- Better reach into Electron/WebView2 surfaces such as editors and web chats.

Recommended proof:

- Test UIA event payloads and supported text-range APIs on Windows Terminal.
- Test `AccessibleObjectFromWindow` or UIA paths against a WebView2/Electron
  surface.
- Preserve `PrintWindow` as fallback.

## Tier 3: Forward-Looking, Verify Before Productizing

### Windows On-Device Registry / Native MCP Connector / Agent ID

What it may give SelfConnect:

- Windows-native discoverability as an agent connector.
- Alignment with policy, identity, and audit systems Microsoft is building.

Next step:

- Verify current public availability and requirements.
- Do not depend on this path until official docs and SDKs are stable.

### MSIX Packaging With Identity

What it gives SelfConnect:

- Package identity.
- Clean install/uninstall.
- Update path.
- Better platform integration for toasts and future connector registration.

Next step:

- Prototype MSIX packaging only after the Python package and MCP path are stable.

### Windows AI APIs / Phi Silica / Windows ML / ONNX + DirectML

What it gives SelfConnect:

- Local model execution on supported CPU/GPU/NPU hardware.
- Lower external API dependency for local workflows.

Next step:

- Treat this as an optional local-model adapter, not a core dependency.
- Probe hardware and model availability before enabling.

### Arm64EC Builds

What it gives SelfConnect:

- Better performance and battery behavior on Arm Windows systems.
- Mixed x64 dependency compatibility for native components.

Next step:

- Defer until there is a concrete Arm deployment target.

## Tier 4: Niche or Lower Priority

- WTS / Session / Window Station APIs:
  - Needed for service-to-user-session routing, RDP, and multi-session meshes.
- DirectComposition layered overlay:
  - Possible delta-region visual path, but lower ROI now that UIA works.
- WER, crash dumps, and performance counters:
  - Useful for production observability.
- Shell extension or `selfconnect://` protocol handler:
  - Potentially useful for file-triggered agents, but high risk because shell
    extensions can destabilize Explorer.

## Architecture Principle

Do not run the whole system elevated all day.

Preferred shape:

- Per-user package and terminal agents run unelevated.
- Policy/control daemon runs as a least-privilege service only if needed.
- Rare privileged actions go through a small broker with interactive consent and
  audit.
- User-profile and HKCU behavior across elevation must be handled explicitly.

## Recommended Next Four

1. ETW provider.
2. TPM key attestation.
3. Job Object + restricted token sandbox.
4. Windows Service + Service SID daemon proof.

The chained channel is the spine. These harden the visibility, identity,
containment, and control-plane legs around it.
