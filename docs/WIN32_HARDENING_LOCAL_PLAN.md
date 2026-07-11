# Win32 Hardening Local Plan

Date: 2026-06-16
Branch: `test/win32-hardening-v1`
Base: `origin/master` at `1337ca4`
Scope: local-only planning and test checkpoint. Do not push unless explicitly approved.

## Session Roles

- `Codex 1`: this tab/session.
- `Role Migration Claude`: spawned Claude session used for migration testing.
- `Claude 1`: separate active Claude session.

Rule: do not inject into a peer unless the target is verified by exact `hwnd`, title/text context, and intended role label.

## Rollback State

- Original branch preserved at `backup/feat-setprop-registry-before-origin-master-20260616-012610`.
- Original untracked artifacts parked at `stash@{1}`.
- Residual observer/session artifacts parked at `stash@{0}`.
- GitHub remote has not been changed.

Rollback command sketch:

```powershell
git switch backup/feat-setprop-registry-before-origin-master-20260616-012610
git stash apply stash@{1}
```

Apply `stash@{0}` only if the residual observer/session artifacts are also needed.

## Test Checkpoint

Passing checks on the GitHub-based local branch:

- `python -m py_compile self_connect.py sc_identity.py sc_firewall.py sc_reliability.py sc_pq.py sc_shell.py sc_resume.py`
- `python test_self_connect.py` -> `68/68 passed`
- `python -m pytest tests -q --ignore=tests/test_trust_layer.py` -> `151 passed, 8 skipped`
- `python -m pytest tests/test_claudego_scanner.py tests/test_claudego_dashboard.py tests/test_approval_partner.py -q` -> `73 passed`
- `tests/test_trust_layer.py` in isolated temp venv with `dilithium-py` -> `87 passed`

Diagnostic failures worth preserving:

- Source environment lacks `dilithium-py`, so PQ trust tests fail unless that dependency is installed.
- Built wheel currently includes only `self_connect.py` at top level; it omits `sc_identity.py`, `sc_firewall.py`, `sc_reliability.py`, `sc_pq.py`, `sc_shell.py`, and `sc_resume.py`.
- Core installed wheel starts `MessageListener`, but the listener thread exits immediately when `pythoncom` is absent.

Post-patch checkpoint on `test/win32-hardening-v1`:

- `_win32_abi.py` added as the centralized, testable Win32 ABI module.
- `self_connect.py` uses `_win32_abi.py` for pointer-sized callbacks/prototypes.
- `MessageListener` survives a core install without `pythoncom`.
- Wheel version is now `0.10.1`.
- Wheel includes `_win32_abi.py`, `self_connect.py`, `sc_identity.py`, `sc_firewall.py`, `sc_reliability.py`, `sc_pq.py`, `sc_shell.py`, and `sc_resume.py`.
- `self_connect.capabilities` is cached at import and exposes `win32`, `uia_text`, `uia_events`, `printwindow`, `named_pipe_impersonation`, and `tpm_identity`.
- `python -m ruff check _win32_abi.py self_connect.py tests/test_win32_hardening.py` -> pass.
- `python -m py_compile _win32_abi.py self_connect.py sc_identity.py sc_firewall.py sc_reliability.py sc_pq.py sc_shell.py sc_resume.py` -> pass.
- `python test_self_connect.py` -> `68/68 passed`.
- `python -m pytest tests -q --ignore=tests/test_trust_layer.py` -> `155 passed, 8 skipped`.
- `tests/test_trust_layer.py` in isolated temp venv with `dilithium-py` -> `87 passed`.
- Installed wheel check: `MessageListener` stays running without `pythoncom`, then stops cleanly.

## Confirmed Win32 Issues

1. HWND ABI risk.
   - Several callbacks use `ctypes.c_int` for HWND/LPARAM on 64-bit Windows.
   - This machine reports `sizeof(HWND)=8`, `sizeof(c_int)=4`, `sizeof(LPARAM)=8`.
   - Fix should centralize Win32 callback/prototype definitions and use `wintypes.HWND`, `wintypes.LPARAM`, `wintypes.BOOL`, `wintypes.WPARAM`, and pointer-sized return types where appropriate.

2. Package layout mismatch.
   - Current wheel version is `0.10.0`.
   - New trust/shell modules exist in source but do not ship in the wheel.
   - Fix should decide module extras instead of forcing trust/PQ dependencies into the core install.

3. Optional dependency crash path.
   - `MessageListener._loop()` imports `pythoncom` unguarded.
   - Core install should degrade instead of crashing a background thread.

4. Enter/submit path inconsistency.
   - `send_string()` routes `\r` through `WM_KEYDOWN/WM_KEYUP`.
   - `submit_claude_input()` uses dual `WM_CHAR 0x0D`.
   - Docs say `WM_KEYDOWN/WM_KEYUP` is ignored by Windows Terminal/Claude Code in some cases.
   - Do not change this blindly; add a live probe first.

5. Peer terminal write collision.
   - During live peer coordination, two agents wrote into the same terminal at once and produced interleaved `WM_CHAR` text.
   - Do not use shared terminal `WM_CHAR` as the coordination/control plane when multiple agents may write concurrently.
   - Additive peer registry/control-plane work must happen beside existing `WM_CHAR` routing before any cutover.

## Win32 Capability Promotion Plan

Promote new experiments as adapters, not rewrites:

- UIA structured read:
  - Add a `StructuredReadChannel` adapter.
  - Prefer TextPattern text where available.
  - Add optional UIA event subscription for push-based reply detection.
  - Keep `PrintWindow` as proof/fallback.

- Named pipe DACL + impersonation:
  - Keep experimental until a daemon/mesh IPC boundary is needed.
  - Promote behind an explicit `ipc` or `secure-ipc` extra.

- TPM/CNG identity:
  - Keep optional and disabled by default.
  - Promote as `HardwareIdentityProvider` behind a `trust`/`tpm` extra.

## Immediate Change Order

1. Add a central Win32 types/prototypes section.
2. Replace `ctypes.c_int` HWND callbacks in core paths.
3. Add small tests that assert callback type sizes on Windows.
4. Guard `MessageListener` optional imports and add a no-UIA fallback test.
5. Fix `pyproject.toml` packaging so intended modules ship with correct extras.
6. Add a capability registry/probe function:

```python
{
    "win32": True,
    "uia_text": bool,
    "uia_events": bool,
    "printwindow": bool,
    "named_pipe_impersonation": bool,
    "tpm_identity": bool,
}
```

7. Add live submit probes before changing Enter behavior.

Completed in local patch:

- Items 1-5.

Still gated:

- Capability registry/probe.
- Additive peer registry/control plane.
- `WM_COPYDATA` pointer round-trip proof before any control-plane use of pointer-carrying messages.

External experiment update from Claude 1:

- UIA TextChanged live-fire proof is met in `experiments/win32_probe/uia_textchanged_fire.py`.
- Proof condition: COM pump running, `WM_CHAR` inject, callback fired with correct text delta.
- This unblocks push-based read work on the next implementation timeline, but polling should remain until the adapter is integrated and tested in this repo.

## Design Rule

Existing working paths stay intact. New Win32 capabilities must be feature-detected adapters with fallback, not replacements. If a capability fails, SelfConnect should step down to the previous known-good channel.
