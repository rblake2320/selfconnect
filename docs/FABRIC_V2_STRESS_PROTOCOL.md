# Fabric V2 Stress Protocol

Private engineering planning note - not approved for public disclosure.

This protocol defines the staged mesh benchmark ladder. It applies first to
Fabric V0/current transport and later to Fabric V2.

## Stress Ladder

```text
5 real             -> capture baseline p50/p95/p99, ACK rate, RAM/VRAM
10 real            -> capture same; compare delta to 5
15 real            -> capture same; watch for nonlinear jump
15 real + 5 logical -> isolate protocol scaling from process overhead
20 real            -> final only if 15+5 is clean
```

The `15 real + 5 logical` rung separates two variables:

- protocol scaling: can the mesh handle 20 participants?
- machine load: can this workstation run 20 heavyweight agent CLIs/browsers?

If `15+5` passes and `20 real` fails, the record should say
`architecture sound, hardware-bound`, not `architecture unproven`.

## Pre-Flight Checklist

- Patent evidence freeze is complete.
- Branch and commit are recorded.
- `selfconnect-mesh verify-events` returns `ok=true`.
- `selfconnect-bench adversarial` has passed.
- Dashboard fleet panel is running.
- All real agents have unique role names and birth IDs.
- Codex/Gemini permission profiles are preconfigured.
- Claude terminals are registered before assignment.
- No unrelated terminals are registered as mesh participants.
- RAM and VRAM are above floors.
- Local-model mode is declared before the run.
- Baseline path is available after the 5-agent run.

## Hard-Stop Rules

| Condition | Action |
| --- | --- |
| first missed ACK | capture window/mailbox state and continue |
| same agent misses ACK twice | mark blocked |
| more than two blocked agents | hard stop |
| wrong-window guard failure | hard stop |
| wrong nonce/hash/sender accepted | hard stop |
| local narration violation twice by same agent | hard stop |
| p99 task latency exceeds 5x 5-agent baseline | hard stop |
| event hash chain fails verification | hard stop |
| RAM below 25 GB while fleet active | halt recommended |
| VRAM below 6 GB while local models active | halt recommended |

Hard stop means:

```text
set halt flag -> stop assigning work -> capture evidence -> do not kill terminals
```

## Cross-Agent Echo Contamination

At 15-20 agents, correctness risk is not only UIA saturation. The serious risk
is accepting output from the wrong sender as a valid response.

Immediate hard stop:

```text
any agent accepts a message whose nonce/hash/sender does not match the expected
sender tuple
```

That condition tests the echo-filter and lease/identity model under scale.

## What Each Rung Proves

| Rung | Evidence value |
| --- | --- |
| 5-10 clean | mesh protocol functions under realistic concurrency |
| 15 clean | no nonlinear degradation in protocol logic |
| 15 real + 5 logical clean | protocol scales to 20 participants |
| 20 real clean | full real-agent deployment viable on this workstation |

The benchmark remains useful even when a rung fails if the halt artifact
identifies whether the limit was protocol, agent drift, permissions, UIA,
resource load, or hardware.
