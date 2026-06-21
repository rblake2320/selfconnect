# Fabric V2 Real-Agent Ladder Results

Date: 2026-06-21

## Verdict

PASS through 20 real visible CLI agents.

This ladder used real visible Windows Terminal windows running real `codex exec`
agent processes. Each rung required UIA readback from each visible window to
contain that agent's expected ACK. This is not the logical harness and not a
simulation.

## Rungs

| Rung | Run ID | Verdict | ACKs | ACK p50 ms | ACK p95 ms | ACK p99 ms | ACK max ms | Missed ACKs | Drift | Approval stalls |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 real | `SC_REAL5_20260621_001643` | PASS | 5/5 | `2301.398` | `2368.903` | `2374.646` | `2376.082` | `0` | `0` | `0` |
| 10 real | `SC_REAL5_20260621_002246` | PASS | 10/10 | `3029.689` | `3135.248` | `3146.907` | `3149.821` | `0` | `0` | `0` |
| 15 real | `SC_REAL5_20260621_002307` | PASS | 15/15 | `4318.245` | `5677.138` | `5704.588` | `5711.451` | `0` | `0` | `0` |
| 20 real | `SC_REAL5_20260621_002328` | PASS | 20/20 | `6173.721` | `7884.952` | `7919.280` | `7927.862` | `0` | `0` | `0` |

## Launch Latency

| Rung | Launch p50 ms | Launch p95 ms | Launch p99 ms | Run duration ms |
| ---: | ---: | ---: | ---: | ---: |
| 5 real | `934.631` | `1017.985` | `1025.061` | `3376.273` |
| 10 real | `1659.302` | `1873.050` | `1889.082` | `4149.988` |
| 15 real | `2734.545` | `3034.839` | `3058.798` | `6711.848` |
| 20 real | `4130.816` | `4614.559` | `4694.177` | `8928.079` |

## Resource Check

After the 20-real rung:

- Free RAM: `70862.281 MB`
- Free VRAM: `28740.0 MB`
- GPU utilization: `2.0%`
- GPU temperature: `47.0 C`

The run stayed above the hard-stop floors.

## Evidence Files

Raw JSON artifacts are local and ignored by Git:

- `experiments/fabric_v2/results/baseline_5agent_real.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_002246.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_002307.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_002328.json`

The mesh event chain also records the 5-real pass and the refreshed v2 baseline.

## Boundary

This proves real-agent ACK/readback scale through 20 visible Codex CLI agents on
this workstation. It does not claim cross-vendor equivalence for Claude/Gemini
or zero-model-call deterministic replay. This benchmark intentionally invokes
one real Codex model call per real ACK task.

The logical Fabric harness remains the source for sub-millisecond
transport/governance latency and zero-model-call known-task results. This real
ladder measures real agent process launch, terminal/UIA readback, and model ACK
latency.
