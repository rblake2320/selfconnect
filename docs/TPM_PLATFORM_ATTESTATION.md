# TPM Platform Attestation

SelfConnect can provision a machine-scoped, non-exportable RSA identity key in
the Windows Microsoft Platform Crypto Provider and use it to issue a
nonce-bound `NCRYPT_CLAIM_PLATFORM` quote.

## Provision

Run from an elevated PowerShell prompt:

```powershell
python -m pip install ".[trust]"
selfconnect-tpm-attestation provision
```

The default key is `SelfConnectPlatformAIK-v1`. Its private material remains in
the platform provider. The command reports only the public-key digest and key
properties.

## Verify The Hardware Path

```powershell
selfconnect-tpm-attestation selftest --output tpm-selftest-redacted.json
```

The selftest provisions an ephemeral identity key, issues a quote covering PCR
0 through PCR 23, verifies its RSA signature and PCR digest, rejects a wrong
nonce, rejects modified PCR evidence, and deletes the ephemeral key.

Application verifiers should generate a fresh 16-64 byte nonce, pin the
provisioned public-key digest, call `verify_and_consume`, and keep the
`NonceReplayStore` on durable local storage. The replay store uses a
cross-process file lock and atomic replacement.

## Proven Boundary

The local verifier proves all of the following:

- the quote is signed by the pinned, non-exportable PCP identity key;
- the TPM quote contains the verifier nonce;
- the selected PCR inventory recomputes to the quoted digest;
- the expected PCR 0-23 selection is bound to the quote;
- the verifier nonce has not already been consumed by the durable replay store.

This is platform-attestation mechanism evidence on the tested Windows TPM 2.0
host. It does not establish manufacturer or endorsement-key certificate-chain
trust, remote fleet enrollment, verifier policy distribution, certificate
revocation, independent assessment, or an authorization to operate. Those are
relying-party and deployment controls.

## Evidence

- `sc_tpm_attestation.py`
- `tests/test_tpm_attestation.py`
- `experiments/win32_probe/results/tpm_platform_attestation_live_20260720_redacted.json`
- `experiments/win32_probe/results/tpm_platform_attestation_selftest_20260720_redacted.json`
