# Fabric V2 Real 5-Agent Baseline

Date: 2026-06-21

## Verdict

PASS.

This is the first real-agent baseline artifact for the Fabric V2 ladder. It is
not a logical simulation. The run launched five visible Windows Terminal
windows, each running a real `codex exec` agent process. PASS required UIA
readback from each visible terminal to contain that agent's expected ACK line.

## Run

- Run ID: `SC_REAL5_20260621_001152`
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

Aggregate:

- Max ACK latency: `2674.729 ms`
- Min ACK latency: `1450.438 ms`
- Total run duration: `3674.944 ms`

## Boundary

This proves the 5-real-agent baseline for visible real CLI agents and UIA
readback. It does not claim that the 10/15/20 real-agent ladder has passed yet.
Those rungs should reuse the same runner/ceremony and compare against
`baseline_5agent_real.json`.
