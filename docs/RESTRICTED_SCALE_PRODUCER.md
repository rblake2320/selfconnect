# Restricted Scale Producer Boundary

`experiments/fabric_v2/restricted_scale_producer.py` is a disabled-by-default,
manually dispatched evidence producer. The draft workflow has not established a
completed scale run and does not establish production, partner, or government
readiness.

## Evidence semantics

- `requested_runner_config` records workflow requests and identifiers. A runner
  group name or requested image digest is not runtime attestation and is not
  evidence that the machine is ephemeral, dedicated, or free of sensitive data.
- `actual_argv_projection` is emitted only after the native provider process
  command line exactly matches the pinned executable and restricted invocation.
  Prompts and temporary paths are checked but omitted from the evidence.
- `actual_environment_names` is the exact sorted name set used to construct the
  provider child's `ProcessStartInfo` environment after clearing inherited
  variables. Values and credentials are never emitted.
- `process_stdout` is the primary ACK observation.
  `rendered_terminal_copy` is a later UIA read of that stdout after the wrapper
  writes it to the terminal. It carries `derivative_of_event_id` and is not an
  independent receiver ACK.

## Cleanup boundary

The runner refuses to start when any Windows Terminal process already exists.
Before a terminal host PID becomes eligible for termination, every observed
Windows Terminal window must be one of the producer's nonce-bound windows. If
that ownership proof is unavailable, cleanup is limited to recorded shell and
provider child PIDs; it does not terminate the terminal host.

This prevents the producer from killing a user's shared Windows Terminal. It
does not prove that the wider machine or runner image is disposable.

## Remaining evidence

The ecosystem consumer must validate the frozen schema, GitHub artifact
attestation, workflow/run identity, and evidence freshness. A real run also
requires a separately administered restricted runner and provider credentials.
No provider or model is invoked by the unit or repository test suites.
