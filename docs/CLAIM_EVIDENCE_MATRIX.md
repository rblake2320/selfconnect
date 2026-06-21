# SelfConnect Claim Evidence Matrix

Last updated: 2026-06-21

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
| Browser UIA live handoff channel | Proven as private defensive snapshot | `docs/PATENT_PRIOR_ART_SNAPSHOT.md`; UIA Value/Invoke worked where Chromium ignored terminal-style channels |
| Lancelot/UAB public overlap boundary | Documented private competitive-defense note | `docs/LANCELOT_UAB_COMPETITIVE_DEFENSE.md`; avoid generic desktop-control claims and preserve SelfConnect lane around SID-bound leases, target guard, echo-filtered readback, and no-CDP UIA browser proof |
| Historical AWS/DGX/Spark AI-to-AI work | Documented as related private antecedent, not direct SelfConnect proof | `docs/AWS_AI_TO_AI_EVIDENCE_INDEX.md`; December 2025 hub-mediated AI-to-AI milestones, January 2026 teacher-student/DMS patent evidence, February 2026 AI data-ownership backup |
| Patent design-around defense | Documented private counsel-facing strategy | `docs/PATENT_DESIGN_AROUND_DEFENSE.md`; maps likely competitor workarounds to broader claim families and representative embodiments |
| Open defense build targets | Documented private execution queue | `docs/OPEN_DEFENSE_BUILD_TARGETS.md`; ranks channel-router composition proof, strict no-MCP browser rerun, runtime SID lookup, stale-tab proof, signed receipt composition, TPM attestation, ETW, job object, and service SID work |
| Protected checkpoint detection | Proven on local mock checkpoint | `docs/BROWSER_LOCAL_PROOF.md`, browser PASS artifact |
| CAPTCHA bypass | Not claimed | `docs/BROWSER_LOCAL_PROOF.md`, `docs/PROVEN_VS_UNTESTED.md` |
| Named pipe + DACL + impersonation | Proven in experiment/enterprise lane | `experiments/win32_probe/CAPABILITY_BACKLOG.md`; production DACL hardening still pending |
| Pipe-authenticated role leases/generations | Proven as isolated control-plane proof | `sc_mesh_lease.py`, `experiments/win32_probe/pipe_role_lease_probe.py`, redacted PASS artifact |
| Governed lease gate on guarded send/read path | Proven as optional in-process runtime gate | optional runtime governed enforcement on the guarded send/read path (role+birth_id+generation+hwnd+owner_sid_hash) layered over the explore-mode target guard; `sc_mesh_lease.evaluate_lease_gate`, `sc_cli.send_text_to_window`/`read_window`, `sc_mcp` tools, `tests/test_mesh_lease.py`, `tests/test_package_adapters.py`. Boundary: OPTIONAL, in-process, NOT a full daemon; explore mode unchanged (no-op); birth_id optional in gate (checked when provided, skipped when omitted for backward compat); runtime OS SID lookup is the next step (currently injectable / fails closed on `<unknown-sid>`) |
| Channel-router composition proof | Proven as redacted model proof plus live throwaway/local proof | `experiments/win32_probe/channel_router_composition_probe.py`, `experiments/win32_probe/results/channel_router_composition_PASS_redacted.json`, `experiments/win32_probe/results/channel_router_composition_LIVE_PASS_redacted.json`, `tests/test_channel_router_composition.py`, `docs/CHANNEL_ROUTER_COMPOSITION_PROOF.md`. Boundary: deterministic model proof selects terminal `WM_CHAR`, browser UIA Value/Invoke, metadata file registry, governed lease gate, stale-denial, wrong-target denial, and echo-filtered readback; live proof composes throwaway terminal `TextChanged_event` and isolated local browser UIA-class read/write without MCP; strict public-browser no-keyboard rerun remains separate |
| TPM/CNG key use | Proven in experiment/enterprise lane | `experiments/win32_probe/CAPABILITY_BACKLOG.md`; full attestation pending |
| TPM platform attestation | Pending | `NCryptCreateClaim` descriptor fix still required |
| ETW provider smoke | Proven as isolated probe | `experiments/win32_probe/etw_provider.py`, `CAPABILITY_BACKLOG.md` |
| Service SID daemon posture | Documented/probed, not productized | `experiments/win32_probe/service_sid_probe.py`, `SERVICE_SID_DAEMON.md` |
| Job Object containment | Proven in experiment/enterprise lane | `CAPABILITY_BACKLOG.md`; runtime adapter pending |
| MCP/package distribution | Proven as optional adapter | `sc_mcp.py`, `pyproject.toml`, package tests, built wheel inspection. Boundary: MCP is not required for the proven WM_CHAR terminal path, UIA browser path, echo-filtered readback, target guard, or lease-gate model |
| Fabric V0 logical scale harness | Proven as benchmark/evidence harness | `sc_fabric_benchmark.py`, `docs/FABRIC_V2_BENCHMARK_RESULTS.md`; 5/10/15/20 logical agents passed with flat sub-ms transport/governance p99 and `0.0` model calls per known task |
| Fabric adversarial gates | Proven as benchmark guard suite | `selfconnect-bench adversarial`; wrong nonce/sender/hash/window, replay, stale lease, narration drift, ACK loss, queue depth, event-log failure, tamper, resource halt, and 1000-message logical load all covered |
| Fabric V2 sign-once/MAC-many frame layer | Proven as first V2 implementation slice | `sc_fabric_v2.py`, `tests/test_fabric_v2.py`, `experiments/fabric_v2/results/fabric_v2_selftest_20260621_073951_redacted.json`, `experiments/fabric_v2/results/fabric_v2_5agent_baseline_redacted.json`; HMAC frame sealing, receiver binding, payload hash, replay rejection, deadline rejection, bounded mailbox backpressure, and real Windows named-pipe ACK selftest |
| Fabric V2 IOCP-dispatched host ACK path | Proven as host-service proof slice | `sc_fabric_host.py`, `tests/test_fabric_host.py`, `experiments/fabric_v2/results/fabric_v2_host_selftest_20260621_074925_redacted.json`; long-lived local named-pipe host, IOCP completion queue in ACK path, bounded mailbox, replay rejection |
| Fabric V2 direct overlapped pipe IO | Proven as focused data-plane proof | `sc_fabric_host.overlapped_named_pipe_exchange`, `tests/test_fabric_host.py`, `experiments/fabric_v2/results/fabric_v2_overlapped_pipe_selftest_20260621_080840_redacted.json`; client and server both use `FILE_FLAG_OVERLAPPED`, IOCP read/write completions, HMAC frames, bounded mailbox, and replay rejection. Boundary: long-lived per-user router/crash recovery remains open |

## Positioning Boundary

The current defensible position is:

> SelfConnect has proven an OS-native Windows AI mesh over desktop surfaces,
> with guarded targeting, structured readback, echo suppression, role tracking,
> local browser fixture control, and optional governed assurance probes.

The current non-claims are:

- generic universal app bridge / first desktop-control bridge;
- first AI-to-AI communication through desktop automation;
- unrestricted browser automation across public sites;
- CAPTCHA bypass or anti-bot evasion;
- production TPM attestation;
- production named-pipe control-plane replacement for terminal-visible routing;
- full service-mode governed daemon.
- production Fabric V2 per-user router/crash recovery service.

## Next Highest-Value Evidence

1. Done (optional in-process gate): role leases/generation IDs including birth_id
   are wired into governed `sc_cli send`/`read` and MCP `send_text`/`read_window`
   as an opt-in gate. Remaining: resolve the current OS owner SID at runtime
   (`OpenProcessToken` -> `GetTokenInformation(TokenUser)` ->
   `ConvertSidToStringSid`) so governed mode no longer requires an injected SID.
2. Finish production Fabric V2 per-user router and crash/restart proof.
3. Finish TPM platform attestation with correct `NCryptBufferDesc`.
4. Add browser multi-tab/stale-tab proof.
5. Add governed audit event for protected checkpoint pause.
6. Wrap job-object containment as an optional runtime adapter.
