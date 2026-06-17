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

## What It Does Not Yet Do

- It is not yet wired into `sc_cli send` or `selfconnect-mesh heartbeat`.
- It does not yet replace visible terminal routing.
- It uses a proof-local named pipe, not a long-running service daemon.
- It hashes/redacts the OS caller SID instead of storing the raw SID.
- It does not yet apply a custom DACL in this proof; production should restrict
  the pipe security descriptor to trusted users/services.

## Next Runtime Step

Add optional lease validation to `sc_cli send` and MCP `send_text`:

```text
send_text requires either:
  - explore mode: target guard only; or
  - governed mode: target guard + current role lease.
```

For governed agents, `role + generation + hwnd + owner SID` should be checked
before any UI fallback write/read action.
