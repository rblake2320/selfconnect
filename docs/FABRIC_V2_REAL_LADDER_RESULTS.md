# Fabric V2 Real-Agent Ladder Results

Date: 2026-06-21

## Verdict

PASS through 20 real visible Codex CLI agents.

PASS through 20 real mixed visible CLI agents using authenticated providers
available on this workstation: 10 Codex + 10 Claude.

This ladder used real visible Windows Terminal windows running real `codex exec`
and/or `claude -p` agent processes. Each rung required UIA readback from each
visible window to contain a standalone exact ACK line for that agent. This is
not the logical harness and not a simulation.

## Rungs

| Rung | Run ID | Verdict | ACKs | ACK p50 ms | ACK p95 ms | ACK p99 ms | ACK max ms | Missed ACKs | Drift | Approval stalls |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 real | `SC_REAL5_20260621_011131` | PASS | 5/5 | `4540.131` | `5638.907` | `5643.165` | `5644.230` | `0` | `0` | `0` |
| 20 real | `SC_REAL5_20260621_011140` | PASS | 20/20 | `10286.688` | `13024.686` | `13828.675` | `14029.672` | `0` | `0` | `0` |

## Launch Latency

| Rung | Launch p50 ms | Launch p95 ms | Launch p99 ms | Run duration ms |
| ---: | ---: | ---: | ---: | ---: |
| 5 real | `990.835` | `1086.513` | `1094.927` | `6644.802` |
| 20 real | `4427.701` | `4898.740` | `4939.833` | `15029.895` |

Mixed exact-line launch latency:

| Rung | Launch p50 ms | Launch p95 ms | Launch p99 ms | Run duration ms |
| ---: | ---: | ---: | ---: | ---: |
| 5 mixed | `1214.032` | `1324.877` | `1334.781` | `22626.523` |
| 10 mixed | `2158.107` | `2892.569` | `2926.035` | `31774.216` |
| 15 mixed | `3168.915` | `3526.832` | `3556.505` | `42822.978` |
| 20 mixed | `3940.608` | `4367.332` | `4419.071` | `59158.251` |

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
| preflight | `SC_PROVIDER_PREFLIGHT_20260621_011029` | Codex + Claude + Gemini | FAIL | 2/3 ready | n/a | n/a | n/a | n/a | n/a | `0` | `1` |
| 1 smoke | `SC_REAL5_20260621_003308` | 1 Gemini | FAIL | 0/1 | n/a | n/a | n/a | n/a | `1` | `0` | `1` |
| 5 mixed | `SC_REAL5_20260621_011156` | 3 Codex + 2 Claude | PASS | 5/5 | `6958.033` | `21621.477` | `21625.034` | `21625.924` | `0` | `0` | `0` |
| 10 mixed | `SC_REAL5_20260621_011220` | 5 Codex + 5 Claude | PASS | 10/10 | `25623.109` | `30763.294` | `30771.810` | `30773.938` | `0` | `0` | `0` |
| 15 mixed | `SC_REAL5_20260621_011254` | 8 Codex + 7 Claude | PASS | 15/15 | `11751.627` | `41091.218` | `41676.188` | `41822.430` | `0` | `0` | `0` |
| 20 mixed | `SC_REAL5_20260621_011338` | 10 Codex + 10 Claude | PASS | 20/20 | `30458.832` | `57182.844` | `57962.971` | `58158.003` | `0` | `0` | `0` |

Two failed attempts were intentionally kept as learning evidence:

- `SC_REAL5_20260621_003633`: 15 mixed, 14/15 ACKs. Claude changed the
  requested role name in its output. The runner now classifies this as
  `wrong_ack_format` when the nonce appears but the exact expected ACK does not.
- `SC_REAL5_20260621_004812`: 20 mixed, 19/20 ACKs. The runner matched
  `realclaude-1` against `realclaude-10` because the title finder used substring
  matching. The finder now requires a role boundary and has regression coverage.

The 15 and 20 mixed rungs were rerun after those fixes and passed.

After the substring ACK issue was found, the Codex endpoint and every mixed rung
above were rerun with standalone exact-line ACK matching. Those exact-line runs
are the final evidence rows in this document.

## Evidence Files

Raw JSON artifacts are local and ignored by Git:

- `experiments/fabric_v2/results/baseline_5agent_real.json`
- `experiments/fabric_v2/results/baseline_5agent_real_claude2_codex3.json`
- `experiments/fabric_v2/results/provider_preflight_SC_PROVIDER_PREFLIGHT_20260621_011029.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_011131.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_011140.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_011156.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_011220.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_011254.json`
- `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_011338.json`
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
