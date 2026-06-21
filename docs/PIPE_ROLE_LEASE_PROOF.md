# Pipe-Authenticated Role Lease Proof

Last updated: 2026-06-17

This proof addresses the highest-risk mesh control-plane gap: visible terminal
text should not be the source of truth for routing, migration, or UI fallback
authorization.

## Concept

Each mesh role receives a lease from a sidecar control plane. The lease contains:

- mesh name;
- role name;
- registry birth ID / instance identity;
- monotonic generation;
- lease ID;
- HWND;
- PID;
- exe/class/title hash;
- OS caller SID hash;
- expiry time.

When a role migrates to a new HWND, the control plane issues a higher
generation and the mesh registry issues a new `birth_id`. Any future UI fallback
action must present:

`role + birth_id + generation + hwnd + owner SID`

If any field is stale, the action fails closed.

## Why This Is Patent-Relevant

The useful combination is not merely "named pipes" or "window automation."

The useful combination is:

1. AI agents operate through visible desktop/terminal surfaces.
2. A separate OS-native control plane owns routing authority.
3. The control plane authenticates a role lease request through Windows named
   pipe impersonation.
4. The lease carries a monotonic generation tied to the role and current HWND.
5. UI fallback is permitted only when the lease tuple still matches.
6. Migration invalidates old generations and stale HWNDs automatically.

That makes terminal-visible communication a fallback data plane, not the source
of routing truth.

## Current Proof

`experiments/win32_probe/pipe_role_lease_probe.py` runs a local named-pipe
roundtrip and proves:

- pipe request is bound to an OS caller identity via
  `ImpersonateNamedPipeClient`;
- initial role lease starts at generation 1;
- migrated lease advances to generation 2;
- current generation/HWND is allowed;
- stale generation is rejected;
- stale HWND is rejected;
- lease renewal is allowed only for the current tuple.

The live artifact is:

`experiments/win32_probe/results/pipe_role_lease_PASS_redacted.json`

Current live result:

- `verdict`: `PASS`
- `impersonated`: `true`
- `initial_generation`: `1`
- `migrated_generation`: `2`
- `current_allowed`: `true`
- `stale_generation_rejected`: `true`
- `stale_hwnd_rejected`: `true`
- `renewed`: `true`

Implementation note: the server reads the client message before calling
`ImpersonateNamedPipeClient`, because Windows impersonates the client that sent
the last message on that pipe instance. The client also opens the pipe with
SQOS impersonation flags.

## Wired Into The Shippable Send/Read Path (optional gate)

The lease gate is now wired into the shippable guarded send/read path as an
**opt-in, in-process runtime gate** — not a full service daemon:

- Trigger: `profile="governed"` OR explicit lease fields (`role`/`generation`)
  on `sc_cli.send_text_to_window` / `sc_cli.read_window` and the matching MCP
  tools (`send_text` / `read_window`).
- Layering: the explore-mode target guard still runs first and is still
  required; the lease gate is checked **after** the target guard passes and
  **before** any write (send) or at the **start** of a governed read.
- Explore mode is byte-for-byte unchanged: when no governed signal is present
  the lease gate is a no-op ALLOW and the existing return shapes are untouched
  (governed read with no signal does not even add an `ok` key).
- Checked tuple: `role + birth_id + generation + hwnd + owner_sid_hash` via
  `RoleLeaseTable.validate_ui_fallback`. `birth_id` is optional at call time
  (checked when provided, skipped when omitted for backward compatibility).
- No raw SID: only `owner_sid_hash` flows into any returned `lease_gate` dict;
  the raw SID is hashed/redacted (tested in `tests/test_mesh_lease.py`).
- Fail-closed: if the runtime OS SID is unavailable and none is injected,
  `current_owner_sid` returns the sentinel `"<unknown-sid>"`, which never
  matches a real issued lease, so governed mode denies.
- Evidence: `sc_mesh_lease.evaluate_lease_gate`, `tests/test_mesh_lease.py`,
  `tests/test_package_adapters.py`.

Current-SID runtime integration is now proven:
`OpenProcessToken` -> `GetTokenInformation(TokenUser)` ->
`ConvertSidToStringSidW`. Governed callers may still inject `owner_sid` for
tests/control-plane handoffs, but the Windows runtime path can now resolve the
current process SID directly and still fails closed on `"<unknown-sid>"`.

## What It Does Not Yet Do

- The gate is in-process and optional; it does not yet replace visible terminal
  routing, and it is not a long-running service daemon.
- Runtime OS owner-SID resolution is implemented on Windows and still fails
  closed on unsupported platforms or Win32 failure.
- `selfconnect-mesh heartbeat` does not yet consult the gate.
- It uses a proof-local named pipe, not a long-running service daemon.
- It hashes/redacts the OS caller SID instead of storing the raw SID.
- It does not yet apply a custom DACL in this proof; production should restrict
  the pipe security descriptor to trusted users/services.

## Next Runtime Step

Optional lease validation is now wired into `sc_cli send`/`read` and MCP
`send_text`/`read_window`:

```text
send_text / read_window resolve to either:
  - explore mode (default): target guard only (no-op lease gate); or
  - governed mode (profile=governed or explicit role/generation):
        target guard + current role lease.
```

For governed agents, `role + generation + hwnd + owner SID hash` is checked
before any UI fallback write/read action. Runtime owner-SID resolution is proven
in `experiments/win32_probe/runtime_sid_probe.py` and redacted artifact
`experiments/win32_probe/results/runtime_sid_probe_PASS_redacted.json`.
