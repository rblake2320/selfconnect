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

## 2026-07-17 - Restricted Scale Producer Consumer Pin

- Added the disabled, manually dispatched restricted 10/15/20 producer tracked
  by issue #21. Unit and repository tests do not launch providers or models.
- Pinned the reviewed consumer contract to selfconnect-ecosystem main merge
  `f07b4a72ca900da531a8596168600977a6678427` in both code and workflow.
- Canonicalized producer-source hashing as strict UTF-8 with CRLF/CR converted
  to LF, then regenerated the deterministic five-file compatibility fixture.
- The current fixture binds its manifest consumer pin and generator identity;
  the merged ecosystem consumer accepted the regenerated bytes through its
  production validator.
- Validation: 37 focused tests; 657 repository tests passed, 9 skipped, and the
  independently tracked issue #23 hang test was deselected; Ruff, compilation,
  and diff checks passed. No workflow or provider dispatch occurred.
- Rollback: revert the producer PR. Do not restore a feature-branch consumer
  SHA or the former placeholder as an executable release contract.
