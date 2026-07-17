# Restricted Scale Producer Boundary

`experiments/fabric_v2/restricted_scale_producer.py` is a disabled-by-default,
manually dispatched evidence producer. The draft workflow has not established a
completed scale run and does not establish production, partner, or government
readiness.

## Evidence semantics

- `producer_context` records factual GitHub workflow/run identity.
  `requested_runner_config` contains only the requested environment and runner
  group. A runner group name is not runtime attestation and is not
  evidence that the machine is ephemeral, dedicated, or free of sensitive data.
- `actual_argv_projection` is emitted only after the native provider process
  command line exactly matches the pinned executable and restricted invocation.
  It retains the nonce-bound prompt and normalizes only executable and temporary
  policy paths to basenames.
- `constructed_initial_environment_names` is the exact sorted name set used to construct the
  provider child's `ProcessStartInfo` environment after clearing inherited
  variables. It is construction evidence, not a native readback from the child.
  Values and credentials are never emitted.
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

Cleanup records each owned root's PID together with its native creation time,
resolved executable, and Windows session while the process is live. It
revalidates that identity before both terminate and kill. A PID that has exited
and been reused is skipped, and completed provider PIDs are never queued as
cleanup roots.

This prevents the producer from killing a user's shared Windows Terminal. It
does not prove that the wider machine or runner image is disposable.

## Remaining evidence

The ecosystem consumer must validate the frozen schema, GitHub artifact
attestation, workflow/run identity, and evidence freshness. A real run also
requires a separately administered restricted runner and provider credentials.
No provider or model is invoked by the unit or repository test suites.

## Consumer-first release sequence

The producer pins the reviewed ecosystem consumer contract at
`f07b4a72ca900da531a8596168600977a6678427`, the immutable merge commit on consumer `main`.
The workflow must present that exact value. A feature-branch SHA or a different
main commit is not accepted as this producer's contract pin.
