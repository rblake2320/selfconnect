# Fabric V2 Real-Terminal Ladder Protocol

Last updated: 2026-06-20

Private engineering planning note - not approved for public disclosure.

This protocol starts only after the logical ladder and adversarial suite are
green. Logical agents prove protocol/governance scale. Real agents prove the
harder desktop failures: drift, permissions, terminal discovery, UIA readback,
process pressure, and compaction.

## Required Pre-Flight

- Full pytest is green.
- Logical ladder has passed.
- Fault-injection suite has passed.
- Tamper suite has passed.
- Resource halt simulation has passed.
- 100 and 1000 messages-per-agent logical load have passed.
- `selfconnect-mesh verify-events` returns `ok=true`.
- Dashboard fleet panel is running.
- Raw benchmark artifacts are local-only unless explicitly promoted.

## Real Baseline

The first real run must write its own baseline:

```text
experiments/fabric_v2/results/baseline_5agent_real.json
```

Do not use the logical `baseline_5agent.json` for real-terminal runs. Real
agents include terminal I/O, UIA readback, model/CLI latency, permissions, and
possibly inference. Their p99 will be orders of magnitude higher than logical
transport/governance p99.

## Rungs

```text
5 real              -> write baseline_5agent_real.json
10 real             -> compare to real baseline
15 real             -> compare to real baseline
15 real + 5 logical -> isolate protocol scaling from process overhead
20 real             -> final only if 15+5 is clean
```

## Capture Schedule

Each rung captures:

- mesh registry snapshot;
- fleet guard state;
- resource snapshot;
- per-agent birth ID/generation;
- ACK counts and missed ACKs;
- local narration/drift count;
- pending approval state;
- UIA/readback method;
- event-chain verification head hash;
- p50/p95/p99 for transport/governance and end-to-end task latency;
- model calls per known task.

## Hard Stops

Hard stop means:

```text
set halt flag -> stop assigning work -> capture evidence -> do not kill terminals
```

Stop immediately if:

- wrong-window guard failure;
- wrong nonce/hash/sender accepted;
- replay accepted;
- stale lease accepted;
- local narration violation twice by the same agent;
- more than two agents are blocked after two missed ACKs;
- p99 task latency exceeds `5x` the real 5-agent baseline;
- event-chain verification fails;
- dashboard/evidence capture fails.

Halt recommended, but do not kill terminals:

- RAM free below 25 GB;
- VRAM free below 6 GB while local model mode is active.

## Discovery Discipline

Before assigning work:

- register every real terminal with unique role and birth ID;
- verify HWND, PID, exe, class, and title;
- exclude unrelated terminals;
- confirm ConPTY peers are visible through discovery;
- confirm minimized or stale terminals are not accepted as live peers;
- confirm each agent can receive and return a one-line ACK through the intended
  SelfConnect channel.

## Pass Criteria

A rung passes only when:

- all expected ACKs arrive;
- no hard-stop rule fires;
- event chain verifies;
- no raw transcript/private prompt is written into a committed artifact;
- every agent status is `success`, `complete`, or explicitly `logical`;
- p99 remains within the allowed baseline multiplier.

If a rung fails, preserve the halt evidence and do not advance to the next rung.
