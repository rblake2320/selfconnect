# SelfConnect Testing Status - 2026-06-21

This is the current Git-tracked status for the Fabric V2 / real-agent test
effort on `test/win32-hardening-v1`.

## Current Verdict

The current branch is test-clean, package-build-clean, and real-agent ladder
clean for the providers that are authenticated on this workstation.

Gemini is now proven in the real-agent path when an API key is supplied in the
process environment and the Gemini CLI auth selector is temporarily set to
`gemini-api-key` for the run. The runner restores the user's original Gemini CLI
settings afterward and does not write secret values to tracked files.

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
| Adversarial suite | `selfconnect-bench adversarial` | PASS, latest run `adversarial_20260621_023543` |
| Mesh event chain | `selfconnect-mesh verify-events` | PASS, 32 events, head `66a303516a8bf39576ffe679ed6747e8b8802ab99a240cdc2e8f8d88cbb36bd1` |
| Stale real-run windows | `sc_cli.list_window_records(query='SC_REAL5_')` | `0` |
| Resource floor | `selfconnect-fleet resources` | RAM/VRAM above floor |

## Real-Agent Exact-Line Results

Final evidence uses standalone exact-line ACK matching. Substring matching is
no longer accepted.

| Run | Providers | Run ID | Result |
| --- | --- | --- | --- |
| Provider preflight | Codex + Claude + Gemini | `SC_PROVIDER_PREFLIGHT_20260621_011029` | Codex ready, Claude ready, Gemini auth-blocked |
| Provider preflight recheck | Codex + Claude + Gemini | `SC_PROVIDER_PREFLIGHT_20260621_023853` | Codex ready, Claude ready, Gemini auth-blocked |
| Gemini auth recheck | Gemini | `SC_PROVIDER_PREFLIGHT_20260621_061132` | Gemini auth-blocked |
| Gemini env-fallback recheck | Gemini | `SC_PROVIDER_PREFLIGHT_20260621_061439` | Gemini auth-blocked |
| Gemini env-only recheck | Gemini | `SC_PROVIDER_PREFLIGHT_20260621_061757` | Gemini auth-blocked under `oauth-personal` |
| Gemini manual API-key mode recheck | Gemini | `SC_PROVIDER_PREFLIGHT_20260621_061851` | Gemini ready |
| Gemini API-key mode recheck | Gemini | `SC_PROVIDER_PREFLIGHT_20260621_062323` | Gemini ready |
| Mixed provider preflight | Codex + Claude + Gemini | `SC_PROVIDER_PREFLIGHT_20260621_062823` | 3/3 ready |
| Gemini 1 | 1 Gemini | `SC_REAL5_20260621_062543` | 1/1 ACK |
| Mixed 3 | 1 Codex + 1 Claude + 1 Gemini | `SC_REAL5_20260621_062940` | 3/3 ACK |
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

## Gemini Auth History And Persistent Readiness Boundary

Gemini requires non-interactive authentication before it can be part of the real
ladder. This is now proven with an ephemeral process-scoped API key plus
temporary `gemini-api-key` CLI mode, while persistent workstation readiness
still depends on intentionally installing one of:

- `GEMINI_API_KEY`, or
- Google Application Default Credentials.

Fresh recheck on 2026-06-21:

- Gemini CLI installed: `0.46.0`
- `GEMINI_API_KEY`: not present
- `GOOGLE_APPLICATION_CREDENTIALS`: not present
- `GOOGLE_CLOUD_PROJECT`: not present
- `CLOUDSDK_CONFIG`: not present
- `gcloud`: not installed on this workstation
- default ADC files under `%APPDATA%\gcloud` and `%USERPROFILE%\.config\gcloud`: not present
- Provider preflight result: `provider_auth_required`

Additional recheck after user reported adding a Gemini key:

- Command:
  `python experiments\fabric_v2\real_agent_baseline.py --preflight-only --agents 1 --providers gemini:1 --timeout 60`
- Run ID: `SC_PROVIDER_PREFLIGHT_20260621_061132`
- Result: `FAIL`, `provider_auth_required`
- Gemini CLI config: OAuth personal account selected, but non-interactive run
  still reports manual authorization required
- Process/User/Machine environment still did not expose `GEMINI_API_KEY` or ADC
  variables to this test process

Env-fallback hardening and recheck:

- Runner scripts now load Gemini auth variables from Process, User, or Machine
  environment scope at runtime before invoking Gemini, without printing or
  embedding secret values in tracked files
- Recheck run ID: `SC_PROVIDER_PREFLIGHT_20260621_061439`
- Result: `FAIL`, `provider_auth_required`
- Process/User/Machine scopes still did not expose `GEMINI_API_KEY`,
  `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, or
  `CLOUDSDK_CONFIG`

Gemini API-key mode hardening and proof:

- A user-supplied API key passed preflight only after the Gemini CLI auth
  selector was switched from `oauth-personal` to `gemini-api-key`
- The runner now supports `--gemini-auth-type gemini-api-key`, which temporarily
  updates `~/.gemini/settings.json`, runs the provider check, then restores the
  original file bytes in a `finally` block
- The API key is supplied only through the process environment and is removed
  after the run
- `SC_PROVIDER_PREFLIGHT_20260621_062323`: PASS, Gemini ready
- `SC_REAL5_20260621_062543`: PASS, 1 visible Gemini window, exact ACK via UIA
  readback
- `SC_PROVIDER_PREFLIGHT_20260621_062823`: PASS, Codex + Claude + Gemini all
  ready
- `SC_REAL5_20260621_062940`: PASS, 1 Codex + 1 Claude + 1 Gemini, exact ACKs
  through visible windows and UIA readback

Persistent workstation readiness is still separate from ephemeral test
readiness. If no persistent User/Machine environment variable or ADC exists,
readiness tools should continue to report that Gemini needs configuration until
the key or ADC is intentionally installed outside this repository.

For persistent Gemini coverage, rerun:

```powershell
python experiments\fabric_v2\real_agent_baseline.py --preflight-only --agents 3 --providers codex:1,claude:1,gemini:1 --timeout 180 --gemini-auth-type gemini-api-key
python experiments\fabric_v2\real_agent_baseline.py --agents 15 --providers codex:5,claude:5,gemini:5 --timeout 900 --close-windows --gemini-auth-type gemini-api-key
python experiments\fabric_v2\real_agent_baseline.py --agents 20 --providers codex:7,claude:7,gemini:6 --timeout 1200 --close-windows --gemini-auth-type gemini-api-key
```
