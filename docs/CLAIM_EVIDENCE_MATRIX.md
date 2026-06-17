# SelfConnect Claim Evidence Matrix

Last updated: 2026-06-17

This matrix ties current SelfConnect positioning to concrete proof artifacts on
`test/win32-hardening-v1`. It is intentionally conservative: if a capability has
not been live-tested or committed as a probe, it is marked as pending.

| Claim area | Current status | Evidence |
| --- | --- | --- |
| OS-native terminal injection | Proven | `self_connect.send_string`, `test_self_connect.py`, existing WM_CHAR terminal mesh use |
| Window capture readback | Proven | `capture_window`, `save_capture`, `test_self_connect.py` |
| Target-safe sends | Proven in package path | `sc_cli.verify_target`, guarded `send`, `tests/test_package_adapters.py` |
| Mesh role/task/birth tracking | Proven in package path | `sc_mesh_registry.py`, `selfconnect-mesh list/register/update/heartbeat`; birth IDs distinguish terminal instances |
| Explore vs governed profiles | Proven as registry policy metadata | `sc_mesh_registry.py`, `docs/PROVEN_VS_UNTESTED.md` |
| UIA terminal structured read | Proven | `experiments/win32_probe/uia_echo_filter_probe.py`, `docs/UIA_ECHO_FILTER_TERMCONTROL.md` |
| UIA TextChanged event on Windows Terminal | Proven | `experiments/win32_probe/results/uia_echo_filter_PASS_redacted.json` |
| Echo filtering / false-positive suppression | Proven as reusable helper | `sc_echo_filter.py`, `tests/test_uia_echo_filter.py` |
| Browser local page control | Proven on Edge local fixture | `experiments/win32_probe/browser_local_proof.py`, `experiments/win32_probe/results/browser_local_proof_PASS_redacted.json` |
| Protected checkpoint detection | Proven on local mock checkpoint | `docs/BROWSER_LOCAL_PROOF.md`, browser PASS artifact |
| CAPTCHA bypass | Not claimed | `docs/BROWSER_LOCAL_PROOF.md`, `docs/PROVEN_VS_UNTESTED.md` |
| Named pipe + DACL + impersonation | Proven in experiment/enterprise lane | `experiments/win32_probe/CAPABILITY_BACKLOG.md`; production DACL hardening still pending |
| Pipe-authenticated role leases/generations | Proven as isolated control-plane proof | `sc_mesh_lease.py`, `experiments/win32_probe/pipe_role_lease_probe.py`, redacted PASS artifact |
| Governed lease gate on guarded send/read path | Proven as optional in-process runtime gate | optional runtime governed enforcement on the guarded send/read path (role+birth_id+generation+hwnd+owner_sid_hash) layered over the explore-mode target guard; `sc_mesh_lease.evaluate_lease_gate`, `sc_cli.send_text_to_window`/`read_window`, `sc_mcp` tools, `tests/test_mesh_lease.py`, `tests/test_package_adapters.py`. Boundary: OPTIONAL, in-process, NOT a full daemon; explore mode unchanged (no-op); birth_id optional in gate (checked when provided, skipped when omitted for backward compat); runtime OS SID lookup is the next step (currently injectable / fails closed on `<unknown-sid>`) |
| TPM/CNG key use | Proven in experiment/enterprise lane | `experiments/win32_probe/CAPABILITY_BACKLOG.md`; full attestation pending |
| TPM platform attestation | Pending | `NCryptCreateClaim` descriptor fix still required |
| ETW provider smoke | Proven as isolated probe | `experiments/win32_probe/etw_provider.py`, `CAPABILITY_BACKLOG.md` |
| Service SID daemon posture | Documented/probed, not productized | `experiments/win32_probe/service_sid_probe.py`, `SERVICE_SID_DAEMON.md` |
| Job Object containment | Proven in experiment/enterprise lane | `CAPABILITY_BACKLOG.md`; runtime adapter pending |
| MCP/package distribution | Proven | `sc_mcp.py`, `pyproject.toml`, package tests, built wheel inspection |

## Positioning Boundary

The current defensible position is:

> SelfConnect has proven an OS-native Windows AI mesh over desktop surfaces,
> with guarded targeting, structured readback, echo suppression, role tracking,
> local browser fixture control, and optional governed assurance probes.

The current non-claims are:

- unrestricted browser automation across public sites;
- CAPTCHA bypass or anti-bot evasion;
- production TPM attestation;
- production named-pipe control-plane replacement for terminal-visible routing;
- full service-mode governed daemon.

## Next Highest-Value Evidence

1. Done (optional in-process gate): role leases/generation IDs including birth_id
   are wired into governed `sc_cli send`/`read` and MCP `send_text`/`read_window`
   as an opt-in gate. Remaining: resolve the current OS owner SID at runtime
   (`OpenProcessToken` -> `GetTokenInformation(TokenUser)` ->
   `ConvertSidToStringSid`) so governed mode no longer requires an injected SID.
2. Finish TPM platform attestation with correct `NCryptBufferDesc`.
3. Add browser multi-tab/stale-tab proof.
4. Add governed audit event for protected checkpoint pause.
5. Wrap job-object containment as an optional runtime adapter.
