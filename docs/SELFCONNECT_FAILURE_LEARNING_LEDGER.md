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

### 2026-06-21: Gemini CLI Blocked Before Non-Interactive ACK

- Context: cross-vendor probe attempted a visible Gemini CLI ACK with
  `gemini -p` and YOLO approvals.
- Result: no ACK. The raw CLI log reported
  `Manual authorization is required... provide a GEMINI_API_KEY, or ensure
  Application Default Credentials are configured.`
- Decision: mark Gemini as provider-auth-blocked on this workstation, not as a
  SelfConnect transport failure.
- Fix: runner sets Gemini fail-fast environment and classifies this as
  `provider_auth_required` instead of a generic timeout.
- Evidence:
  - local artifact `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_003308.json`

### 2026-06-21: Claude Can Drift From Exact ACK Format Under Load

- Context: first 15 mixed Codex+Claude run returned 14/15 exact ACKs.
- Result: the failed Claude output included the correct nonce but changed the
  role to `realclaude-orchestrator` and added extra fields.
- Decision: this is real-agent drift/wrong-ACK format, not a missing window.
- Fix: prompt strengthened to require the exact line and the runner now
  classifies `nonce present, exact ACK absent` as `wrong_ack_format`.
- Evidence:
  - local artifact `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_003633.json`
  - passing rerun `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_004724.json`

### 2026-06-21: Role-Name Prefix Collisions Can Corrupt Discovery

- Context: first 20 mixed Codex+Claude run returned 19/20 exact ACKs.
- Result: `realclaude-1` matched the `realclaude-10` window because the title
  finder used substring matching.
- Decision: window discovery must use role boundaries, not substring matching.
- Fix: `_title_matches()` now requires exact title or whitespace-delimited
  suffix, and regression tests cover `realclaude-1` vs `realclaude-10`.
- Evidence:
  - local artifact `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_004812.json`
  - passing rerun `experiments/fabric_v2/results/real_agent_baseline_SC_REAL5_20260621_010359.json`

### 2026-06-21: ACK Substring Matching Is Not Strong Enough

- Context: provider output can include the expected ACK inside a prompt echo or
  as a prefix of a longer line such as `ACK ... status=ready`.
- Result: substring matching could overstate a PASS even when the agent did not
  emit the required standalone ACK line.
- Decision: final real-agent evidence must require a standalone line whose
  stripped text equals the expected ACK exactly.
- Fix: `_has_exact_line()` now backs both provider preflight and visible UIA
  readback. Regression tests reject prompt-embedded and suffix-extended ACKs.
- Evidence:
  - exact-line Codex 5/20 reruns:
    `SC_REAL5_20260621_011131`, `SC_REAL5_20260621_011140`
  - exact-line mixed 5/10/15/20 reruns:
    `SC_REAL5_20260621_011156`, `SC_REAL5_20260621_011220`,
    `SC_REAL5_20260621_011254`, `SC_REAL5_20260621_011338`

## Current Learning

- The correct real ladder control path is visible terminal + real CLI process +
  UIA readback with standalone exact-line ACKs, not stale terminal reuse and not
  assuming an injected prompt has been interpreted as authority.
- The 20-real ladder now passes with 0 missed ACKs, 0 drift events, and 0
  approval stalls for Codex-only and for authenticated mixed Codex+Claude.
- Gemini remains blocked until non-interactive auth is configured with
  `GEMINI_API_KEY` or Google Application Default Credentials.
