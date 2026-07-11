# Fabric V2 Real 5-Agent Baseline

Date: 2026-06-21

## Verdict

PASS. The latest baseline uses schema
`selfconnect.real_agent_baseline.v3` with standalone exact-line ACK matching.

This is the first real-agent baseline artifact for the Fabric V2 ladder. It is
not a logical simulation. The run launched five visible Windows Terminal
windows, each running a real `codex exec` agent process. PASS required UIA
readback from each visible terminal to contain that agent's expected ACK line.

## Run

- Latest Run ID: `SC_REAL5_20260621_011131`
- Original Run ID: `SC_REAL5_20260621_001152`
- Runner: `experiments/fabric_v2/real_agent_baseline.py`
- Command: `python experiments\fabric_v2\real_agent_baseline.py --agents 5 --providers codex:5 --timeout 360 --close-windows`
- Real agent CLI: `codex exec`
- Visible windows: yes
- UIA readback required: yes
- Logical simulation: no
- Baseline artifact: `experiments/fabric_v2/results/baseline_5agent_real.json`
- Per-run artifact: `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_011131.json`

The raw JSON artifacts are local/ignored so they do not churn Git history.
This document is the sanitized Git-traceable summary.

## Results

| Agent | HWND | Status | ACK latency ms |
| --- | ---: | --- | ---: |
| `realcodex-1` | redacted | PASS | exact-line ACK observed |
| `realcodex-2` | redacted | PASS | exact-line ACK observed |
| `realcodex-3` | redacted | PASS | exact-line ACK observed |
| `realcodex-4` | redacted | PASS | exact-line ACK observed |
| `realcodex-5` | redacted | PASS | exact-line ACK observed |

Latest aggregate from `SC_REAL5_20260621_011131`:

- ACK p50: `4540.131 ms`
- ACK p95: `5638.907 ms`
- ACK p99: `5643.165 ms`
- ACK max: `5644.230 ms`
- Missed ACKs: `0`
- Drift/narration events: `0`
- Approval stalls: `0`
- Wrong ACK format: `0`
- Provider auth required: `0`

Original aggregate from `SC_REAL5_20260621_001152` remains useful as the first
real pass:

- Max ACK latency: `2674.729 ms`
- Min ACK latency: `1450.438 ms`

The runner now records full latency distributions and exact-line matching for
later rungs:

- ACK latency: `min`, `max`, `mean`, `p50`, `p95`, `p99`
- Launch latency: `min`, `max`, `mean`, `p50`, `p95`, `p99`
- Failure counters: missed ACKs, missing visible windows, UIA readback
  failures, wrong-window failures, drift/narration events, approval stalls,
  wrong ACK format, provider auth required
- Model-call accounting

## Boundary

This proves the 5-real-agent baseline for visible real CLI agents and UIA
readback using standalone exact-line ACK matching. The 10/15/20 real-agent
ladder is summarized in `docs/FABRIC_V2_REAL_LADDER_RESULTS.md`.

This real-agent baseline intentionally invokes one real Codex model turn per
visible agent. It does not claim the zero-model-call deterministic replay
property. That property remains tracked separately by the logical/execution
hierarchy harness.
