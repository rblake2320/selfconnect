# SelfConnect Win32 Hardening And Patent Notes

Date/time: 2026-06-16 02:39:40 -05:00  
Final wrap-up: 2026-06-16 02:44:44 -05:00  
Branch: `test/win32-hardening-v1`  
Repository: `https://github.com/rblake2320/selfconnect.git`  
Status: branch pushed to GitHub for laptop access; not merged into `master`.

## Executive Summary

Today the SelfConnect mesh converged on a concrete Win32 hardening path and implemented the first set of changes on a dedicated branch.

The most important result is that the core working SelfConnect transport was not replaced. The working `WM_CHAR`, `PrintWindow`, UIA, ClaudeGo, and trust-layer paths were preserved while the fragile parts were hardened:

- Win32 ABI definitions were centralized and made pointer-sized.
- Packaging was fixed so the new trust and shell modules ship in the wheel.
- `MessageListener` no longer dies in a clean core install without `pythoncom`.
- `self_connect.capabilities` now exposes cached probe results for Win32, UIA, PrintWindow, named-pipe impersonation, and TPM identity.
- A composed experiment, `chained_channel.py`, was added to prove the chain between UIA read events, identity signing, and named-pipe verification.

GitHub was intentionally left unchanged until the user asked to preserve the work for laptop access. The correct artifact to push is the branch `test/win32-hardening-v1`, not `master`.

## Active Agents / Roles

- `Codex 1`: this Codex tab/session.
- `Role Migration Claude` / `RMC`: spawned Claude role-migration session.
- `Claude 1`: independent Claude session.

Important lesson: live peer coordination through shared terminal `WM_CHAR` can collide. Two agents wrote to the same terminal at the same time and produced interleaved text. This confirms routing and migration metadata should move to an additive control plane, while user-facing agent conversation can continue using `WM_CHAR`.

## Commits On This Branch

`2a3027a docs: capture local win32 hardening plan`

- Added `docs/WIN32_HARDENING_LOCAL_PLAN.md`.
- Captured rollback state, tests, risk list, and staged change order.

`0e1415c fix: harden win32 ABI and packaging`

- Added `_win32_abi.py`.
- Added pointer-sized Win32 callback/prototype definitions.
- Added visible `_WIN32_TYPES` section in `self_connect.py`.
- Replaced risky `ctypes.c_int` callback use in core paths with pointer-sized handle callbacks.
- Preserved pointer-sized `SendMessage`/`SendMessageTimeout` paths.
- Kept the existing `WM_CHAR` injection path intact.
- Added `self_connect.capabilities`, cached at import:
  - `win32`
  - `uia_text`
  - `uia_events`
  - `printwindow`
  - `named_pipe_impersonation`
  - `tpm_identity`
- Fixed `MessageListener` so it survives without `pythoncom`.
- Updated packaging to version `0.10.1`.
- Added package includes for:
  - `_win32_abi.py`
  - `self_connect.py`
  - `sc_identity.py`
  - `sc_firewall.py`
  - `sc_reliability.py`
  - `sc_pq.py`
  - `sc_shell.py`
  - `sc_resume.py`
- Added optional extras:
  - `trust`
  - `pq`
  - `shell`
- Added `tests/test_win32_hardening.py`, including `0xDEADBEEFCAFEBABE` handle-value preservation.

`da18050 experiments: chained_channel.py - composed UIA + identity + named pipe proof`

- Added `experiments/win32_probe/chained_channel.py`.
- Composes:
  - UIA TextChanged / TextPattern read path.
  - message hashing.
  - Ed25519 identity signing through `sc_identity`.
  - named-pipe verification path.
- Notes TPM upgrade path with `NCryptCreateClaim` / hardware-backed identity as future work.

`a25d4b2 experiments: fix chained_channel.py B1/B2/B3 + impersonation + safe target`

- Fixed UIA wrapper loading with `comtypes.client.GetModule("UIAutomationCore.dll")`.
- Fixed named-pipe handle ABI:
  - `CreateNamedPipeW.restype = ctypes.c_void_p`.
  - `CreateFileW.restype = ctypes.c_void_p`.
  - `INVALID_HANDLE_VALUE` checked with pointer-width `ctypes.c_size_t(-1).value`.
- Fixed UIA event dispatch by using the full prefixed callback name:
  - `IUIAutomationEventHandler_HandleAutomationEvent`.
- Added Role B `ImpersonateNamedPipeClient` coverage for the OS-verified caller leg.
- Added explicit Ed25519 software-signing note and TPM upgrade path through `NCryptCreateClaim`.
- Added a throwaway `conhost.exe` target so live-fire probes do not write into unrelated agent windows.

`1ebba94 fix: ruff cleanup on chained_channel.py (ClassVar, f-string, import order)`

- Cleaned the experiment file enough for `ruff` to pass.
- Fixed Role A target selection so an explicit `--target` stays an integer HWND instead of becoming a tuple.
- Preserved the same chained-channel behavior while making the saved GitHub copy runnable.

## Verification Results

Passing checks after the hardening patch:

- `python -m ruff check _win32_abi.py self_connect.py tests/test_win32_hardening.py`
  - Passed.
- `python -m py_compile _win32_abi.py self_connect.py sc_identity.py sc_firewall.py sc_reliability.py sc_pq.py sc_shell.py sc_resume.py`
  - Passed.
- `python test_self_connect.py`
  - `68/68 passed`.
- `python -m pytest tests -q --ignore=tests/test_trust_layer.py`
  - `155 passed, 8 skipped`.
- `tests/test_trust_layer.py` in isolated temp venv with `dilithium-py`
  - `87 passed`.
- Wheel build / install check:
  - Built `selfconnect-0.10.1`.
  - Wheel contains all intended modules.
  - Clean core install without `pythoncom` keeps `MessageListener` running and stopping cleanly.
- `experiments/win32_probe/chained_channel.py`
  - Compiles clean.

## Win32 Conclusions

1. Fixing Win32 ABI was the correct first change.
   - The fix is not a blind rewrite of every integer.
   - The important boundary is where handles and pointer-carrying values cross `ctypes`.
   - `WM_COPYDATA` and other pointer-carrying `LPARAM` paths are the highest risk for future work.

2. Do not churn the working `WM_CHAR` terminal path.
   - Existing injection works.
   - It should remain the user-visible data-plane transport.
   - Routing metadata should move beside it, not replace it immediately.

3. Capability probing is the right architecture.
   - New Win32 features should be feature-detected adapters.
   - If a capability fails, SelfConnect should fall back to the last proven channel.

4. UIA TextChanged is validated externally.
   - Claude 1 reported proof that UIA TextChanged fired twice on live terminal output with correct delta, including a minimized-window case.
   - The proof file was reported as `experiments/win32_probe/uia_textchanged_fire.py`.
   - Push-based read is now a valid next implementation target, but polling should remain until the adapter is integrated and tested in this repo.

5. Named pipes and TPM should remain optional for now.
   - Named-pipe DACL + impersonation is the correct future control-plane/IPC boundary.
   - TPM/CNG identity is a strong enterprise/government proof, but should remain optional and disabled by default.

## Patent Claim Notes

These are technical notes for later patent-counsel review, not legal advice.

### Claim Family 1: OS-Native AI-To-AI Terminal Transport

Core idea:

- An AI agent communicates with another AI agent by injecting text into an existing native Windows terminal/window input surface using OS message delivery, such as `PostMessage(WM_CHAR)`.
- The receiving agent reads the message from its own normal UI/session rather than an API endpoint.
- This creates an AI-to-AI transport without HTTP, WebSocket, JSON-RPC, API keys, browser automation, or a broker.

Current proof basis:

- Existing SelfConnect `send_string()` / `send_frame()` paths.
- Framing layer over `WM_CHAR`.
- `PrintWindow`/UIA receive paths.

### Claim Family 2: Semantic Read Channel With Event-Driven Reply Detection

Core idea:

- Instead of reading pixels only, the system uses Windows UI Automation / TextPattern to extract structured text from a target AI session.
- It subscribes to UIA text/change events to detect that a target agent has emitted new output.
- The system then reads the changed text delta semantically, without OCR or full-screen capture.

Current proof basis:

- `get_text_uia()` in `self_connect.py`.
- Claude 1's reported `uia_textchanged_fire.py` proof: COM pump running, `WM_CHAR` injection, callback fired with correct text delta.

### Claim Family 3: Governed Control Plane Separate From User-Facing Terminal Plane

Core idea:

- User-visible task text can travel through terminal/window injection.
- Routing, migration, peer identity, and health metadata travel through a separate local control plane.
- The control plane may be implemented with a file registry, named pipe, or named-pipe DACL broker.
- This avoids terminal text collision and prevents routing data from polluting agent conversations.

Current proof basis:

- WM_CHAR collision observed during live multi-agent coordination.
- Role migration and peer registry design discussions.
- Named-pipe DACL + impersonation proof reported by the mesh.

### Claim Family 4: OS-Attested Local Agent IPC

Core idea:

- A receiving component does not trust a claimed identity string in the payload.
- It verifies the caller using the Windows security boundary, such as named-pipe impersonation and SID inspection.
- The system rejects or ignores spoofed payload identities when the OS-verified caller identity does not match.

Current proof basis:

- Named pipe DACL + `ImpersonateNamedPipeClient` proof reported by RMC/Claude.
- Planned additive registry/control-plane work.

### Claim Family 5: Hardware-Backed Agent Identity

Core idea:

- An agent identity can be bound to hardware using TPM/CNG-backed keys.
- The private key does not leave the hardware provider.
- Audit events or message hashes can be signed by the hardware-backed key.

Current proof basis:

- TPM ECDSA P-256 sign/verify proof reported by the mesh.
- `sc_identity.py` Ed25519 currently used in `chained_channel.py`.
- Future upgrade path: `NCryptCreateClaim` for stronger platform attestation.

### Claim Family 6: Composed Chained Channel

Core idea:

- A single governed loop composes:
  1. OS-native event detection.
  2. structured text delta extraction.
  3. message hashing.
  4. agent signing.
  5. OS-verified local IPC delivery.
  6. signature and caller verification by the receiver.

Current proof basis:

- `experiments/win32_probe/chained_channel.py`.
- UIA TextChanged proof.
- named-pipe identity proof.
- Ed25519 signing through `sc_identity.py`.
- future TPM/CNG attestation path noted.

## Next Work

1. Live-fire the full `chained_channel.py` path:
   - B listens on pipe.
   - A injects into Claude 1 terminal.
   - UIA TextChanged fires.
   - A signs the message hash.
   - B verifies signature and caller/control-plane message.

2. Add additive peer registry/control plane:
   - Start with JSON file or named pipe sidecar.
   - Do not remove `WM_CHAR` routing until three-agent discovery is proven.

3. Add `WM_COPYDATA` pointer round-trip proof:
   - Confirm a 64-bit pointer-bearing `LPARAM` round-trips safely.
   - Gate any future `WM_COPYDATA` control-plane path on this proof.

4. Integrate UIA push read adapter:
   - Keep polling fallback.
   - Do not replace polling globally until adapter is tested inside this repo.

5. Keep all new enterprise/security-heavy features optional:
   - named pipe impersonation
   - TPM/CNG identity
   - post-quantum signatures
   - chain verification

## Rollback / Restore Notes

Original local state before updating to GitHub master:

- Backup branch: `backup/feat-setprop-registry-before-origin-master-20260616-012610`.
- Original untracked artifacts: `stash@{1}`.
- Residual observer/session artifacts: `stash@{0}`.

Current branch to access from laptop after push:

```powershell
git fetch origin
git switch test/win32-hardening-v1
```

If the branch is not created locally yet:

```powershell
git fetch origin test/win32-hardening-v1:test/win32-hardening-v1
git switch test/win32-hardening-v1
```
