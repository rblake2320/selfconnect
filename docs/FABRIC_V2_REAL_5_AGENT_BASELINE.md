# Fabric V2 Real 5-Agent Baseline

Date: 2026-06-21

## Verdict

PASS. The latest baseline uses schema
`selfconnect.real_agent_baseline.v2`.

This is the first real-agent baseline artifact for the Fabric V2 ladder. It is
not a logical simulation. The run launched five visible Windows Terminal
windows, each running a real `codex exec` agent process. PASS required UIA
readback from each visible terminal to contain that agent's expected ACK line.

## Run

- Latest Run ID: `SC_REAL5_20260621_001643`
- Original Run ID: `SC_REAL5_20260621_001152`
- Runner: `experiments/fabric_v2/real_agent_baseline.py`
- Command: `python experiments\fabric_v2\real_agent_baseline.py --agents 5 --timeout 360`
- Real agent CLI: `codex exec`
- Visible windows: yes
- UIA readback required: yes
- Logical simulation: no
- Baseline artifact: `experiments/fabric_v2/results/baseline_5agent_real.json`
- Per-run artifact: `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_001152.json`

The raw JSON artifacts are local/ignored so they do not churn Git history.
This document is the sanitized Git-traceable summary.

## Results

| Agent | HWND | Status | ACK latency ms |
| --- | ---: | --- | ---: |
| `realcodex-1` | `72749678` | PASS | `2674.729` |
| `realcodex-2` | `28971544` | PASS | `1450.438` |
| `realcodex-3` | `177082618` | PASS | `1493.941` |
| `realcodex-4` | `107221574` | PASS | `1541.992` |
| `realcodex-5` | `65475008` | PASS | `1588.176` |

Latest aggregate from `SC_REAL5_20260621_001643`:

- ACK p50: `2301.398 ms`
- ACK p95: `2368.903 ms`
- ACK p99: `2374.646 ms`
- ACK max: `2376.082 ms`
- Launch p50: `934.631 ms`
- Launch p95: `1017.985 ms`
- Total run duration: `3376.273 ms`
- Missed ACKs: `0`
- Drift/narration events: `0`
- Approval stalls: `0`

Original aggregate from `SC_REAL5_20260621_001152` remains useful as the first
real pass:

- Max ACK latency: `2674.729 ms`
- Min ACK latency: `1450.438 ms`

The runner now records full latency distributions for later rungs:

- ACK latency: `min`, `max`, `mean`, `p50`, `p95`, `p99`
- Launch latency: `min`, `max`, `mean`, `p50`, `p95`, `p99`
- Failure counters: missed ACKs, missing visible windows, UIA readback
  failures, wrong-window failures, drift/narration events, approval stalls
- Model-call accounting

## Boundary

This proves the 5-real-agent baseline for visible real CLI agents and UIA
readback. It does not claim that the 10/15/20 real-agent ladder has passed yet.
Those rungs should reuse the same runner/ceremony and compare against
`baseline_5agent_real.json`.

This real-agent baseline intentionally invokes one real Codex model turn per
visible agent. It does not claim the zero-model-call deterministic replay
property. That property remains tracked separately by the logical/execution
hierarchy harness.
