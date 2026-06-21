# SelfConnect Failure Learning Ledger

Date opened: 2026-06-21

Purpose: keep the lessons from failed probes and rejected benchmark attempts in
GitHub, without storing raw private transcripts or noisy local artifacts.

## Entries

### 2026-06-20: Stale Terminals Are Not Valid Real Benchmark Agents

- Context: early 5-real attempt reused existing terminal windows.
- Result: only 3/5 exact ACKs returned.
- Decision: rejected as invalid; no `baseline_5agent_real.json` was created.
- Fix: require fresh run-id tagged terminals and/or real CLI sidecar execution.
- Evidence:
  - local artifact `experiments/fabric_v2/results/real5_baseline_20260620_215157_redacted.json`
  - mesh event `real_baseline_probe_invalidated`

### 2026-06-20: Injected Prompt Echo Can Look Like ACK

- Context: an ACK detector matched text that was present in the injected prompt.
- Result: false positive risk.
- Decision: ACK checks must be echo-safe: the exact expected ACK string must not
  appear in the prompt, or the detector must compare against pre-send readback.
- Fix: later runner required UIA readback and exact expected ACK observation per
  visible window.

### 2026-06-20: Fresh Claude Interactive TUI Rejected Role Assignment

- Context: fresh Claude windows were asked through injected text to become
  `realprobe-*` benchmark roles.
- Result: Claude refused to accept identity/role authority from injected prompt
  text, even with bypass permissions enabled.
- Decision: bypass permissions and role authority are separate concerns.
- Fix: do not use interactive Claude TUI prompts as the primary ACK control
  plane for the real ladder; use supported CLI/programmatic modes.

### 2026-06-20: Codex Bypass Flag Conflict

- Context: first Codex probe used
  `--dangerously-bypass-approvals-and-sandbox -a never -s danger-full-access`.
- Result: Codex exited because the bypass flag conflicts with explicit
  `--ask-for-approval`.
- Fix: use `codex exec --dangerously-bypass-approvals-and-sandbox` by itself for
  the real CLI ACK runner.

### 2026-06-21: Real-Agent Baseline Needed Distribution Metrics

- Context: first 5-real pass captured min/max ACK latency only.
- Result: not enough for 10/15/20 degradation comparison.
- Fix: schema `selfconnect.real_agent_baseline.v2` now records p50/p95/p99,
  launch distributions, failure counters, and model-call accounting.

## Current Learning

- The correct real ladder control path is visible terminal + real CLI process +
  UIA readback, not stale terminal reuse and not assuming an injected prompt has
  been interpreted as authority.
- The 20-real ladder now passes with 0 missed ACKs, 0 drift events, and 0
  approval stalls.
- Cross-vendor behavior still needs its own ladder; the current real ladder is
  Codex CLI only.
