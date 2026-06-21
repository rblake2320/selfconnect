# Fabric Benchmark Results

Last updated: 2026-06-21

Private engineering evidence summary - not approved for public disclosure.

## Current Transport V0 Logical Production Ladder

This run measured the current SelfConnect transport/governance path before
Fabric V2. It used logical agents rather than real CLI terminals, so it proves
the benchmark harness, event-chain persistence, profile matrix, fleet guard, and
baseline comparison path. It does not replace the later real-terminal 5/10/15
stress run.

Pre-flight:

- Full pytest suite: `411 passed, 28 skipped`
- Patent freeze check: `ok=true`
- Branch: `test/win32-hardening-v1`
- Head before run: `7dbf172`
- Profiles: `normal`, `enterprise`, `government`
- Messages per agent: `3`
- Raw prompt/transcript content included: `false`

## Results

| Rung | Verdict | Baseline use | Transport/governance p99 | Audit/event p99 | End-to-end p99 | Model calls per known task |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 5 logical | pass | wrote `baseline_5agent.json` | 0.032 ms | 74.695 ms | 152.594 ms | 0.0 |
| 10 logical | pass | loaded 5-agent baseline | 0.044 ms | 104.834 ms | 201.080 ms | 0.0 |
| 15 logical | pass | loaded 5-agent baseline | 0.047 ms | 120.835 ms | 219.500 ms | 0.0 |
| 20 logical | pass | loaded 5-agent baseline | 0.045 ms | 103.004 ms | 203.825 ms | 0.0 |

## Event Chain Verification

Each run wrote a local JSONL event chain. All chains verified cleanly.

| Rung | Events checked | Verify result |
| --- | ---: | --- |
| 5 logical | 120 | ok |
| 10 logical | 240 | ok |
| 15 logical | 360 | ok |
| 20 logical | 480 | ok |

## Interpretation

The current transport logical harness scales cleanly through 20 logical agents
with no model calls for known tasks and no p99 regression against the 5-agent
baseline. The heavy cost is event/audit persistence, not the in-process
transport/governance path.

The result that matters most for the current logical ladder is:

```text
5 logical transport/governance p99  -> 0.032 ms
20 logical transport/governance p99 -> 0.045 ms
```

That is near-zero marginal governance cost from 5 to 20 logical participants.
It supports the claim that policy/evidence evaluation is not the bottleneck in
the logical control plane.

## Adversarial And Load Pass

After the logical ladder, the adversarial suite was run against the same
benchmark path.

| Suite | Result | Coverage |
| --- | --- | --- |
| Fault injection | pass | wrong nonce, wrong sender, wrong hash, wrong window, replay, stale lease, narration drift, ACK loss, queue-depth, event-log failure |
| Tamper detection | pass | modified, deleted, and reordered event rows all failed verification |
| Resource halt simulation | pass | RAM floor, VRAM floor with local model mode, VRAM ignored without local model mode |
| Logical load | pass | 5 agents at 100 and 1000 messages per agent |

Load details:

| Load run | Logical messages | Events checked | Transport p99 | Audit p99 | End-to-end p99 | Model calls per known task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 agents x 100 messages | 500 | 1,010 | 0.018 ms | 0.452 ms | 0.809 ms | 0.0 |
| 5 agents x 1000 messages | 5,000 | 10,010 | 0.022 ms | 0.656 ms | 1.284 ms | 0.0 |

The high-volume pass also validates the optimized event append path: every event
chain still verifies, but appending no longer repeatedly scans the whole JSONL
file.

## Proven Boundary

Proven by this run:

- protocol/governance scale to 20 logical participants;
- fault-injection hard stops fire as expected;
- tamper-evident event chains detect modification, deletion, and reordering;
- resource floors produce halt recommendations;
- 1000-message-per-agent logical load keeps event verification intact;
- known tasks complete with `0.0` model calls.

Not proven by this run:

- real-agent drift and compaction behavior;
- approval stalls across Codex/Gemini/Claude terminals;
- UIA saturation on many live terminal windows;
- real process/RAM/VRAM pressure from 5-20 heavyweight CLIs;
- real terminal discovery reliability across ConPTY, minimized windows, and
  migrated sessions.

This supports the next benchmark step:

```text
5 real -> 10 real -> 15 real -> 15 real + 5 logical -> 20 real if stable
```

The real-terminal ladder is still pending. That run will measure process,
terminal, UIA, permissions, drift, and compaction effects that logical agents do
not exercise.

Use a separate real baseline such as:

```text
experiments/fabric_v2/results/baseline_5agent_real.json
```

Do not compare real-agent p99 latency against the logical
`baseline_5agent.json`; the two measure different bottlenecks.

## Fabric V2 Frame/Mailbox Slice

Fabric V2 has its first benchmarkable implementation slice:

- `sc_fabric_v2.py`
- `selfconnect-fabric selftest`
- `selfconnect-bench run --transport fabric_v2_frame_mailbox`

Implemented in this slice:

- session key derivation from a session secret;
- sign-once/MAC-many frame sealing with HMAC-SHA256;
- payload hashes;
- receiver binding;
- sequence-number replay rejection;
- deadline expiration rejection;
- bounded mailbox backpressure;
- real Windows named-pipe request/ACK selftest through `AF_PIPE`;
- benchmark integration as a separate transport profile.

Boundary:

- This is not yet the production IOCP host service.
- It is a measured Fabric V2 frame/mailbox and named-pipe proof that lets the
  benchmark compare current transport vs Fabric V2 without changing the metric
  schema.
- IOCP service mode, per-user router, and long-lived mailbox host remain the
  next Fabric V2 build target.

Validation:

| Check | Result |
| --- | --- |
| `python -m pytest tests/test_fabric_v2.py tests/test_fabric_v0_benchmark.py -q` | `19 passed` |
| `python -m ruff check sc_fabric_v2.py sc_fabric_benchmark.py tests/test_fabric_v2.py tests/test_fabric_v0_benchmark.py` | PASS |
| `python -m py_compile sc_fabric_v2.py sc_fabric_benchmark.py tests\test_fabric_v2.py tests\test_fabric_v0_benchmark.py` | PASS |
| `python -m sc_fabric_v2 selftest` | PASS, real Windows named-pipe ACK |
| `python -m sc_fabric_host selftest` | PASS, IOCP-dispatched host ACK |
| `python -m sc_fabric_host overlapped-selftest` | PASS, direct overlapped pipe read/write through IOCP |
| `python -m sc_fabric_router selftest` | PASS, replay state survives router restart |
| `selfconnect-bench run --transport fabric_v2_frame_mailbox --agents 5` | PASS |

Fabric V2 5-agent baseline:

| Artifact | Value |
| --- | --- |
| Redacted artifact | `experiments/fabric_v2/results/fabric_v2_5agent_baseline_redacted.json` |
| Baseline | `experiments/fabric_v2/results/baseline_5agent_fabric_v2_frame_mailbox.json` |
| Transport/governance p99 | `0.152 ms` |
| Audit/event p99 | `0.508 ms` |
| End-to-end p99 | `1.297 ms` |
| Model calls per known task | `0.0` |

Fabric V2 named-pipe selftest:

| Artifact | Value |
| --- | --- |
| Redacted artifact | `experiments/fabric_v2/results/fabric_v2_selftest_20260621_073951_redacted.json` |
| Transport | Windows named pipe (`AF_PIPE`) |
| ACK payload | `ACK:selftest-a:1` |
| Elapsed | `0.797 ms` |
| Replay rejection | PASS |

Fabric V2 host selftest:

| Artifact | Value |
| --- | --- |
| Redacted artifact | `experiments/fabric_v2/results/fabric_v2_host_selftest_20260621_074925_redacted.json` |
| Host transport | Windows named pipe through `AF_PIPE` |
| Completion dispatch | Win32 IOCP `PostQueuedCompletionStatus` / `GetQueuedCompletionStatus` |
| First ACK payload | `ACK:host-selftest-a:1` |
| First roundtrip | `0.494 ms` |
| Total selftest elapsed | `2.568 ms` |
| Replay rejection | PASS |
| Boundary | IOCP dispatch is in the ACK path; direct overlapped named-pipe read/write remains next hardening step |

Fabric V2 direct overlapped pipe selftest:

| Artifact | Value |
| --- | --- |
| Redacted artifact | `experiments/fabric_v2/results/fabric_v2_overlapped_pipe_selftest_20260621_080840_redacted.json` |
| Host transport | Windows named pipe with `FILE_FLAG_OVERLAPPED` |
| Completion dispatch | Win32 IOCP `GetQueuedCompletionStatus` for read/write completions |
| Server read/write | Overlapped read and overlapped write |
| Client read/write | Overlapped read and overlapped write |
| First ACK payload | `ACK:overlapped-a:1` |
| First roundtrip | `0.781 ms` |
| Replay rejection | PASS |
| Boundary | Router replay-state restart proof is recorded below; queued mailbox payload recovery remains open |

Fabric V2 router restart selftest:

| Artifact | Value |
| --- | --- |
| Redacted artifact | `experiments/fabric_v2/results/fabric_v2_router_restart_selftest_20260621_081434_redacted.json` |
| Redacted state snapshot | `experiments/fabric_v2/results/fabric_v2_router_state_20260621_081434_redacted.json` |
| Agent count | `3` |
| Accepted sequence count after restart | `4` |
| Replay rejected after restart | PASS |
| New post-restart route | PASS, `ACK:router-a:2` |
| Raw payload text included | `false` |
| Boundary | Replay state recovery is proven; queued mailbox payload recovery is not claimed |
