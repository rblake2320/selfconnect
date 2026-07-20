from __future__ import annotations

import json

import pytest
import sc_tpm_attestation as tpm


def test_nonce_replay_store_is_durable(tmp_path) -> None:
    nonce = b"n" * 32
    path = tmp_path / "nonces.json"
    tpm.NonceReplayStore(path).consume(nonce, now=1000)

    with pytest.raises(tpm.TpmAttestationReplayError):
        tpm.NonceReplayStore(path).consume(nonce, now=1001)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == {tpm._sha256(nonce): 1000}


def test_nonce_replay_store_prunes_expired_entries(tmp_path) -> None:
    path = tmp_path / "nonces.json"
    store = tpm.NonceReplayStore(path, retention_s=60)
    store.consume(b"a" * 32, now=1000)
    store.consume(b"b" * 32, now=1061)
    assert json.loads(path.read_text(encoding="utf-8")) == {tpm._sha256(b"b" * 32): 1061}


def test_strict_artifact_rejects_extra_fields() -> None:
    artifact = {
        "schema_version": 1,
        "claim_type": "tpm-platform",
        "provider": tpm.PROVIDER,
        "key_name": "key",
        "public_key_blob_b64u": "AA",
        "public_key_sha256": "0" * 64,
        "claim_b64u": "AA",
        "claim_sha256": "0" * 64,
        "nonce_sha256": "0" * 64,
        "pcr_mask": tpm.DEFAULT_PCR_MASK,
        "issued_at": 1,
        "hardware_backed": True,
        "private_key_exportable": False,
        "pcp_identity_key": True,
        "extra": True,
    }
    with pytest.raises(tpm.TpmAttestationVerificationError, match="shape"):
        tpm._strict_artifact(artifact)


def test_platform_claim_parser_rejects_bad_magic() -> None:
    with pytest.raises(tpm.TpmAttestationVerificationError, match="TPM quote"):
        tpm._parse_quote(b"\x00" * 80)


@pytest.mark.skipif(tpm.sys.platform != "win32", reason="requires Windows TPM")
def test_hardware_selftest_when_explicitly_enabled(monkeypatch) -> None:
    if not tpm.os.environ.get("SELFCONNECT_TEST_TPM_ATTESTATION"):
        pytest.skip("set SELFCONNECT_TEST_TPM_ATTESTATION=1 for destructive TPM selftest")
    result = tpm.hardware_selftest()
    assert result["ok"] is True
    assert result["quote_verified"] is True
    assert result["nonce_mismatch_rejected"] is True
    assert result["tampered_pcr_rejected"] is True
    assert result["manufacturer_chain_verified"] is False
