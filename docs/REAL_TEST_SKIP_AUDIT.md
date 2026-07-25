# Real-test skip audit

Last audited: 2026-07-25 on Windows, full suite result `921 passed, 16
skipped`.

SelfConnect does not replace missing integration prerequisites with mocks,
fake services, synthetic model replies, or unconditional passes. A skip is
acceptable only when the real prerequisite is unavailable and the reason is
reported.

| Count | Tests | Real prerequisite | Disposition |
|---:|---|---|---|
| 8 | `test_antigravity_controller.py` integration cases | A running, ready, and for chat cases authenticated Antigravity render surface | Unavailable in this desktop session. Starting an unauthenticated substitute or faking its UI would not test the contract. These remain explicitly skipped, not passed. |
| 6 | `test_guarded_submit_windows.py` | An unlocked interactive desktop and explicit `SELFCONNECT_REAL_INTERACTIVE=1` consent for foreground input | The default suite does not seize the user's foreground window. The same two real tests are parameterized for three iterations each and were separately run successfully on this machine (`6 passed`) during point-of-use hardening. |
| 1 | `test_tpm_attestation.py` | Elevated permission to finalize a real TPM machine key | The installed TPM returned `0x80090010`. This non-elevated session cannot manufacture that authority; software-key substitution would invalidate the hardware claim. |
| 1 | `test_uia_echo_filter.py` | A non-Windows host | This test exercises the non-Windows not-applicable branch. Running on Windows is the opposite platform condition, so the skip is the asserted platform matrix behavior. |

The M5 visual-specialist gate has no skips. It is backed by three independent
real runs using a native owned Win32 window, UIA, OCR, Qwen3-VL through Ollama,
measured RTX 5090 admission, Qwen 3.6 27B unload/restore, and semantic Win32
button activation.

When prerequisites become available, run the real surfaces rather than
altering these guards:

```powershell
$env:SELFCONNECT_REAL_INTERACTIVE = "1"
python -m pytest tests/test_guarded_submit_windows.py -q

python -m pytest tests/test_antigravity_controller.py -q -rs
python -m pytest tests/test_tpm_attestation.py -q -rs
```
