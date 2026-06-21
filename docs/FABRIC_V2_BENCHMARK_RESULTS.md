# Fabric Benchmark Results

Last updated: 2026-06-20

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

This supports the next benchmark step:

```text
5 real -> 10 real -> 15 real -> 15 real + 5 logical -> 20 real if stable
```

The real-terminal ladder is still pending. That run will measure process,
terminal, UIA, permissions, drift, and compaction effects that logical agents do
not exercise.
