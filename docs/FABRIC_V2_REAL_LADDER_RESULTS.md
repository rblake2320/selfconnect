# Fabric V2 Real-Agent Ladder Results

Date: 2026-06-21

## Verdict

PASS through 20 real visible Codex CLI agents.

PASS through 20 real mixed visible CLI agents using authenticated providers
available on this workstation: 10 Codex + 10 Claude.

This ladder used real visible Windows Terminal windows running real `codex exec`
and/or `claude -p` agent processes. Each rung required UIA readback from each
visible window to contain that agent's expected ACK. This is not the logical
harness and not a simulation.

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

## Cross-Vendor Rungs

Authenticated provider coverage:

- Codex: PASS in one-shot probe and visible ladder.
- Claude: PASS in one-shot probe and visible ladder.
- Gemini: blocked before model execution because Gemini CLI non-interactive
  auth is not configured on this workstation. The CLI returned
  `Manual authorization is required... provide a GEMINI_API_KEY, or ensure
  Application Default Credentials are configured.`

| Rung | Run ID | Providers | Verdict | ACKs | ACK p50 ms | ACK p95 ms | ACK p99 ms | ACK max ms | Missed ACKs | Wrong ACK | Provider auth |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 smoke | `SC_REAL5_20260621_003240` | 1 Codex + 1 Claude | PASS | 2/2 | `13153.273` | `20824.184` | `21506.042` | `21676.507` | `0` | `0` | `0` |
| 1 smoke | `SC_REAL5_20260621_003308` | 1 Gemini | FAIL | 0/1 | n/a | n/a | n/a | n/a | `1` | `0` | `1` |
| 5 mixed | `SC_REAL5_20260621_003518` | 3 Codex + 2 Claude | PASS | 5/5 | `18172.997` | `21503.808` | `21686.640` | `21732.349` | `0` | `0` | `0` |
| 10 mixed | `SC_REAL5_20260621_003549` | 5 Codex + 5 Claude | PASS | 10/10 | `20065.985` | `33397.959` | `34668.060` | `34985.586` | `0` | `0` | `0` |
| 15 mixed | `SC_REAL5_20260621_004724` | 8 Codex + 7 Claude | PASS | 15/15 | `4763.204` | `40615.021` | `41191.536` | `41335.665` | `0` | `0` | `0` |
| 20 mixed | `SC_REAL5_20260621_010359` | 10 Codex + 10 Claude | PASS | 20/20 | `30801.068` | `57087.581` | `57107.396` | `57112.350` | `0` | `0` | `0` |

Two failed attempts were intentionally kept as learning evidence:

- `SC_REAL5_20260621_003633`: 15 mixed, 14/15 ACKs. Claude changed the
  requested role name in its output. The runner now classifies this as
  `wrong_ack_format` when the nonce appears but the exact expected ACK does not.
- `SC_REAL5_20260621_004812`: 20 mixed, 19/20 ACKs. The runner matched
  `realclaude-1` against `realclaude-10` because the title finder used substring
  matching. The finder now requires a role boundary and has regression coverage.

The 15 and 20 mixed rungs were rerun after those fixes and passed.

## Evidence Files

Raw JSON artifacts are local and ignored by Git:

- `experiments/fabric_v2/results/baseline_5agent_real.json`
- `experiments/fabric_v2/results/baseline_5agent_real_claude2_codex3.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_002246.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_002307.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_002328.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_003240.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_003308.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_003518.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_003549.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_003633.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_004724.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_004812.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_010359.json`

The mesh event chain also records the 5-real pass and the refreshed v2 baseline.

## Boundary

This proves real-agent ACK/readback scale through 20 visible Codex CLI agents and
through 20 visible mixed Codex+Claude CLI agents on this workstation. It does
not claim Gemini equivalence until Gemini non-interactive auth is configured and
rerun. It does not claim zero-model-call deterministic replay. This benchmark
intentionally invokes one real provider model call per real ACK task.

The logical Fabric harness remains the source for sub-millisecond
transport/governance latency and zero-model-call known-task results. This real
ladder measures real agent process launch, terminal/UIA readback, and model ACK
latency.
