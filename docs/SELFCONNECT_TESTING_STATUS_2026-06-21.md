# SelfConnect Testing Status - 2026-06-21

This is the current Git-tracked status for the Fabric V2 / real-agent test
effort on `test/win32-hardening-v1`.

## Current Verdict

The current branch is test-clean, package-build-clean, and real-agent ladder
clean for the providers that are authenticated on this workstation.

Gemini is not yet included in the real ladder because Gemini CLI
non-interactive authentication is not configured on this workstation. This is
recorded as `provider_auth_required`, not as a SelfConnect transport failure.

## Verified Gates

| Gate | Evidence | Result |
| --- | --- | --- |
| Full Python suite | `python -m pytest -q` | `434 passed, 28 skipped` |
| Ruff/compile for real ladder runner | `ruff check` + `py_compile` | PASS |
| Source doctor | `python -m sc_cli doctor --json` | `0.10.4`, Win32/UIA/TPM platform probes true |
| Wheel build | `python -m build` | `selfconnect-0.10.4` sdist + wheel built |
| Installed package | `pip install --force-reinstall --no-deps dist/selfconnect-0.10.4-py3-none-any.whl` | installed `0.10.4` |
| Installed CLI doctor | `selfconnect doctor --json` outside repo | PASS |
| Patent freeze gate | `selfconnect-bench freeze-check` in repo | PASS |
| Adversarial suite | `selfconnect-bench adversarial` | PASS |
| Mesh event chain | `selfconnect-mesh verify-events` | PASS, 32 events |
| Stale real-run windows | `sc_cli.list_window_records(query='SC_REAL5_')` | `0` |
| Resource floor | `selfconnect-fleet resources` | RAM/VRAM above floor |

## Real-Agent Exact-Line Results

Final evidence uses standalone exact-line ACK matching. Substring matching is
no longer accepted.

| Run | Providers | Run ID | Result |
| --- | --- | --- | --- |
| Provider preflight | Codex + Claude + Gemini | `SC_PROVIDER_PREFLIGHT_20260621_011029` | Codex ready, Claude ready, Gemini auth-blocked |
| Codex 5 | 5 Codex | `SC_REAL5_20260621_011131` | 5/5 ACK |
| Codex 20 | 20 Codex | `SC_REAL5_20260621_011140` | 20/20 ACK |
| Mixed 5 | 3 Codex + 2 Claude | `SC_REAL5_20260621_011156` | 5/5 ACK |
| Mixed 10 | 5 Codex + 5 Claude | `SC_REAL5_20260621_011220` | 10/10 ACK |
| Mixed 15 | 8 Codex + 7 Claude | `SC_REAL5_20260621_011254` | 15/15 ACK |
| Mixed 20 | 10 Codex + 10 Claude | `SC_REAL5_20260621_011338` | 20/20 ACK |

## Failure Lessons Now Covered

- Stale terminal reuse is invalid for real-agent benchmarks.
- Prompt echo and ACK substrings are not accepted.
- Claude interactive TUI role assignment is not the benchmark control path.
- Provider readiness must be preflighted before visible ladders.
- Window discovery must distinguish `role-1` from `role-10`.
- Provider output must preserve exact provider, role, nonce, spacing, and field
  names.

## Remaining External Blocker

Gemini needs non-interactive authentication before it can be part of the real
ladder:

- `GEMINI_API_KEY`, or
- Google Application Default Credentials.

After that is configured, rerun:

```powershell
python experiments\fabric_v2\real_agent_baseline.py --preflight-only --agents 3 --providers codex:1,claude:1,gemini:1 --timeout 90
python experiments\fabric_v2\real_agent_baseline.py --agents 15 --providers codex:5,claude:5,gemini:5 --timeout 900 --close-windows
python experiments\fabric_v2\real_agent_baseline.py --agents 20 --providers codex:7,claude:7,gemini:6 --timeout 1200 --close-windows
```

