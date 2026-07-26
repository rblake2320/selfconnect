# Real-test skip audit

Last audited: 2026-07-25 on Windows. Before the user-scoped TPM correction the
full suite result was `944 passed, 16 skipped`; the TPM hardware test now runs
and passes, leaving 15 environment/platform skips.

SelfConnect does not replace missing integration prerequisites with mocks,
fake services, synthetic model replies, or unconditional passes. A skip is
acceptable only when the real prerequisite is unavailable and the reason is
reported.

| Count | Tests | Real prerequisite | Disposition |
|---:|---|---|---|
| 8 | `test_antigravity_controller.py` integration cases | A running, ready, and for chat cases authenticated Antigravity render surface | Unavailable in this desktop session. Starting an unauthenticated substitute or faking its UI would not test the contract. These remain explicitly skipped, not passed. |
| 6 | `test_guarded_submit_windows.py` | An unlocked interactive desktop and explicit `SELFCONNECT_REAL_INTERACTIVE=1` consent for foreground input | The default suite does not seize the user's foreground window. The same two real tests are parameterized for three iterations each and were separately run successfully on this machine (`6 passed`) during point-of-use hardening. |
| 1 | `test_uia_echo_filter.py` | A non-Windows host | This test exercises the non-Windows not-applicable branch. Running on Windows is the opposite platform condition, so the skip is the asserted platform matrix behavior. |

The former TPM skip is closed. SelfConnect now provisions a user-scoped
Microsoft Platform Crypto Provider key, and the real unelevated hardware
self-test passed with quote verification, nonce-mismatch rejection, and
tamper rejection. Evidence:
`proofs/capability_os/tpm_user_scope_live_20260725.json`.
On a Windows CI VM that has no Microsoft Platform Crypto Provider, the same
probe reports `0x80090030` and is explicitly skipped because no software TPM,
mock provider, or fabricated quote is substituted. The hardware-tier result
on this machine remains the release evidence.

The M5 visual-specialist, M6 shadow compiler, and M7 integrated governed-Qwen
gates have no skips. M5 is backed by three independent native Win32/UIA/OCR/VLM
runs; M6 by three independent candidates and nine real replays; M7 by three
fresh governed Qwen runs that completed two-step durable tasks.

When prerequisites become available, run the real surfaces rather than
altering these guards:

```powershell
$env:SELFCONNECT_REAL_INTERACTIVE = "1"
python -m pytest tests/test_guarded_submit_windows.py -q

python -m pytest tests/test_antigravity_controller.py -q -rs
python -m pytest tests/test_tpm_attestation.py -q -rs
```
