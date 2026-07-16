# Engineering Log

## 2026-07-15 - Deterministic External-Target Smoke Probe

- Base: `56d5ff1802dca5d4136bcc32fa37aa122d4944dc`.
- Trigger: post-merge Actions run `29465734240` failed 71/72 SDK smoke
  assertions when the first enumerated window belonged to the runner console.
- Root cause: the smoke test derived a broad keyword from `windows[0]`, while
  `find_target` correctly excludes the resolved caller terminal PID.
- Change: the smoke selects a visible window outside the caller PID, requires a
  title query unique among eligible windows, and asserts the exact HWND and PID
  returned. Duplicate titles are skipped in favor of a unique candidate.
- Coverage: focused regression tests cover caller exclusion, duplicate titles,
  and the no-safe-candidate boundary.
- Rollback: revert this smoke-only change; do not weaken `find_target` caller
  exclusion to accommodate the former test oracle.

## 2026-07-15 - ConsoleWindowClass False-Positive Closure

- Base: `a87e490c88c4ccb18ccaac514d018c7bba779d55`.
- Reproduction: `PostMessageW` returned success for verified
  `ConsoleWindowClass` HWND 264110 / PID 32220 / `pwsh.exe`, while no characters
  appeared. Receiver self-send reproduced the same result.
- Root cause: queue acceptance was represented as successful delivery, and
  official callers ignored `send_string` results.
- Change: auto transport now routes `ConsoleWindowClass` through
  `WriteConsoleInputW`, keeps `WM_CHAR` for tested CASCADIA surfaces, requires
  complete record counts, explicitly restores the caller console, and exposes
  structured transport evidence.
- Caller closure: `sc_cli`, `sc_spawn`, `sc_send.py`, `sc_mesh.py`, approval
  automation, ClaudeGo, `WindowPool`, framed sends, migration spawn,
  `local_agent.py`, and `hub_relay.py` stop on missing or failed delivery
  records. Spawn writes text and Enter through one class-selected call.
- Deterministic verification: 600 passed, 9 display-dependent skips; SDK smoke
  72/72; release-scoped Ruff clean; release claim audit and wheel build passed.
- Live verification: isolated `cmd.exe` proof passed with 280/280 input records,
  caller restoration, and an independently observed target-process effect.
- Evidence:
  `experiments/win32_probe/results/console_input_transport_PASS_redacted.json`.
- Rollback: revert the implementation commit recorded by the pull request; do
  not restore the former `ConsoleWindowClass` PostMessage fallback.
