# Engineering Log

## 2026-07-17 - Cross-Thread Claim Lock Release Recovery

- Base: `c01708e30d7c048f1cfb696658c27b2f439e406a`.
- Trigger: issue #23 reported that
  `test_claim_is_exclusive_across_threads` could stop indefinitely on Windows.
- Reproduction: the unchanged test hung in 2 of 11 bounded subprocess runs.
  Faulthandler showed the test thread blocked in an unbounded join while the
  remaining claimant threads waited in `FileLock.acquire`; the thread that had
  owned the lock was no longer running.
- Root cause: `FileLock.release` closed its descriptor, attempted one unlink,
  and swallowed every `OSError`. A transient Windows sharing violation while a
  contender or scanner read the lock file therefore left an orphan containing
  the still-live process PID. Stale detection treated that PID as an active
  owner, and the test's `LockTimeout` retry loop never terminated.
- Change: lock records now include a unique acquisition token registered in
  process. Release verifies ownership and retries deletion to a monotonic
  deadline. A persistent failure raises `LockReleaseError` and retires the
  in-process ownership record, allowing a later claimant to recognize and
  remove the same-process orphan after the sharing conflict clears. Claim-test
  joins are bounded and report stuck thread names.
- Evidence: deterministic transient- and persistent-unlink regressions pass;
  500/500 four-thread contention rounds passed; focused task tests passed
  12/12; full suite passed 623 with 9 environment-bound skips; Ruff passed.
- Boundary: an external actor that continuously denies deletion can still make
  acquisition fail with a bounded lock error. This change prevents silent
  success and indefinite same-process retry; it does not claim availability
  against a permanently hostile filesystem.
- Rollback: revert the issue #23 pull-request commit. Restoring the prior
  swallow-on-release behavior restores the orphan-lock hang.

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

## 2026-07-15 - Public README Claim-to-Evidence Binding

- Base: `5c493300b937a0f912e32a131061a132d2c11fe8`.
- Trigger: issue #13 showed that the old `100.0%` claim figure counted only
  release-ledger rows and did not enumerate public README statements.
- Inventory: 24 retained public capability, boundary, or historical-exercise
  blocks now carry stable `SC-CLAIM` IDs. Each resolves to a unique
  `release/claims.json` entry with statement, scope, boundary, verification
  date, named evidence, evidence hashes where applicable, and an exact README
  excerpt hash.
- Claim corrections: removed the legal-conclusion-style novelty heading;
  replaced universal `proved live` wording with bounded
  `implemented-and-exercised` history; corrected automatic runbook generation
  to a manual trigger; and stopped positioning the two approval prototypes as
  one governed roundtrip.
- Gate: strict claim-block parsing rejects malformed, nested, mismatched,
  duplicate, unregistered, mismapped, and excerpt-hash-mismatched tags. Output
  names the tagged numerator and denominator and labels free-form prose
  classification `PARTIAL`.
- CI: the Windows workflow runs the release claim/package audit as an explicit
  named step in addition to its regression coverage.
- Evidence hashing: normalized `sha256_text` remains available for text, while
  screenshot evidence now uses raw-file `sha256`; the gate rejects ambiguous or
  malformed hash declarations.
- Validation: 14/14 release-gate tests passed; full suite passed 621 with 9
  display-dependent skips; release-scoped Ruff passed; the release audit passed
  47 checks with zero failures; wheel build contained all 31 required modules
  (`f6b2696972ee52d4195157d4718f6549b2183f295237097f99fc0238694d9a00`).
- Test honesty: the first full-suite run transiently failed
  `test_wait_for_state_returns_on_transition` (620 passed). The exact test then
  passed 30/30 isolated repetitions, a standalone full rerun passed 621, and the
  consolidated audit rerun passed 621. The unreproduced flake remains tracked;
  it is not represented as root-caused by this claim-control change.
- Rollback: revert the pull-request commit. Do not restore the old claim
  percentage without also restoring its explicit ledger-only label.

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
