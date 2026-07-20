"""Nonce-bound TPM 2.0 platform attestation for SelfConnect on Windows.

This module provisions a non-exportable RSA identity key in the Microsoft
Platform Crypto Provider, asks Windows for an NCRYPT_CLAIM_PLATFORM quote, and
verifies the returned TPM quote without trusting caller-supplied metadata.

The local verifier proves possession of the provisioned TPM key, freshness,
the selected PCR values, and the quote signature. Manufacturer/EK certificate
chain approval remains a relying-party deployment policy, not an implicit
claim made by this module.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sc_tasks import FileLock

SCHEMA_VERSION = 1
PROVIDER = "Microsoft Platform Crypto Provider"
DEFAULT_KEY_NAME = "SelfConnectPlatformAIK-v1"
DEFAULT_PCR_MASK = 0xFFFFFF
MAX_CLAIM_BYTES = 64 * 1024
NCRYPT_MACHINE_KEY_FLAG = 0x20
NCRYPT_OVERWRITE_KEY_FLAG = 0x80
NCRYPT_CLAIM_PLATFORM = 0x00010000
NCRYPTBUFFER_TPM_PLATFORM_CLAIM_PCR_MASK = 80
NCRYPTBUFFER_TPM_PLATFORM_CLAIM_NONCE = 81
NCRYPT_PCP_IDENTITY_KEY = 0x00000008
NCRYPT_TPM12_PROVIDER = 0x00010000
NCRYPT_IMPL_HARDWARE_FLAG = 0x00000001
TPM_GENERATED_VALUE = 0xFF544347
TPM_ST_ATTEST_QUOTE = 0x8018
TPM_ALG_SHA1 = 0x0004
TPM_ALG_SHA256 = 0x000B
TPM_ALG_SHA384 = 0x000C
TPM_ALG_SHA512 = 0x000D
TPM_ALG_RSASSA = 0x0014
_PLATFORM_MAGIC = b"ALPT"


class TpmAttestationError(RuntimeError):
    """Base error for provisioning, issuance, or verification failures."""


class TpmAttestationVerificationError(TpmAttestationError):
    """The claim did not satisfy cryptographic or policy checks."""


class TpmAttestationReplayError(TpmAttestationVerificationError):
    """The verifier nonce was already consumed."""


class _NCryptBuffer(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [
        ("cbBuffer", ctypes.c_ulong),
        ("BufferType", ctypes.c_ulong),
        ("pvBuffer", ctypes.c_void_p),
    ]


class _NCryptBufferDesc(ctypes.Structure):
    _fields_: ClassVar[list[tuple[str, Any]]] = [
        ("ulVersion", ctypes.c_ulong),
        ("cBuffers", ctypes.c_ulong),
        ("pBuffers", ctypes.POINTER(_NCryptBuffer)),
    ]


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64u(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > MAX_CLAIM_BYTES * 2:
        raise TpmAttestationVerificationError("invalid base64url field")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception as exc:
        raise TpmAttestationVerificationError("invalid base64url field") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _status(label: str, code: int) -> None:
    if code != 0:
        raise TpmAttestationError(f"{label} failed: 0x{code & 0xFFFFFFFF:08x}")


class _NCrypt:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise TpmAttestationError("TPM attestation requires Windows")
        self.dll = ctypes.WinDLL("ncrypt", use_last_error=True)
        p = ctypes.c_void_p
        d = ctypes.c_ulong
        self.open_provider = self._bind(
            "NCryptOpenStorageProvider", [ctypes.POINTER(p), ctypes.c_wchar_p, d]
        )
        self.create_key = self._bind(
            "NCryptCreatePersistedKey",
            [p, ctypes.POINTER(p), ctypes.c_wchar_p, ctypes.c_wchar_p, d, d],
        )
        self.open_key = self._bind(
            "NCryptOpenKey", [p, ctypes.POINTER(p), ctypes.c_wchar_p, d, d]
        )
        self.set_property = self._bind(
            "NCryptSetProperty", [p, ctypes.c_wchar_p, p, d, d]
        )
        self.get_property = self._bind(
            "NCryptGetProperty", [p, ctypes.c_wchar_p, p, d, ctypes.POINTER(d), d]
        )
        self.finalize_key = self._bind("NCryptFinalizeKey", [p, d])
        self.delete_key = self._bind("NCryptDeleteKey", [p, d])
        self.free_object = self._bind("NCryptFreeObject", [p])
        self.export_key = self._bind(
            "NCryptExportKey", [p, p, ctypes.c_wchar_p, p, p, d, ctypes.POINTER(d), d]
        )
        self.create_claim = self._bind(
            "NCryptCreateClaim",
            [p, p, d, ctypes.POINTER(_NCryptBufferDesc), p, d, ctypes.POINTER(d), d],
        )

    def _bind(self, name: str, args: list[Any]) -> Any:
        fn = getattr(self.dll, name)
        fn.argtypes = args
        fn.restype = ctypes.c_long
        return fn


class _Handles:
    def __init__(self, api: _NCrypt, provider: int, key: int = 0) -> None:
        self.api = api
        self.provider = ctypes.c_void_p(provider)
        self.key = ctypes.c_void_p(key)

    def close(self) -> None:
        if self.key:
            self.api.free_object(self.key)
            self.key = ctypes.c_void_p()
        if self.provider:
            self.api.free_object(self.provider)
            self.provider = ctypes.c_void_p()

    def __enter__(self) -> _Handles:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _open_provider(api: _NCrypt) -> ctypes.c_void_p:
    provider = ctypes.c_void_p()
    _status("NCryptOpenStorageProvider", api.open_provider(ctypes.byref(provider), PROVIDER, 0))
    return provider


def _open_key(api: _NCrypt, key_name: str) -> _Handles:
    if not key_name or len(key_name) > 128 or any(ord(ch) < 32 for ch in key_name):
        raise ValueError("invalid TPM key name")
    provider = _open_provider(api)
    key = ctypes.c_void_p()
    try:
        _status(
            "NCryptOpenKey",
            api.open_key(provider, ctypes.byref(key), key_name, 0, NCRYPT_MACHINE_KEY_FLAG),
        )
    except Exception:
        api.free_object(provider)
        raise
    return _Handles(api, provider.value or 0, key.value or 0)


def _get_dword(api: _NCrypt, key: ctypes.c_void_p, name: str) -> int:
    value = ctypes.c_ulong()
    size = ctypes.c_ulong()
    _status(
        f"NCryptGetProperty({name})",
        api.get_property(key, name, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(size), 0),
    )
    if size.value != ctypes.sizeof(value):
        raise TpmAttestationError(f"unexpected {name} property size")
    return int(value.value)


def _export_public_blob(api: _NCrypt, key: ctypes.c_void_p) -> bytes:
    size = ctypes.c_ulong()
    _status(
        "NCryptExportKey(size)",
        api.export_key(key, None, "RSAPUBLICBLOB", None, None, 0, ctypes.byref(size), 0),
    )
    if size.value <= 24 or size.value > 16 * 1024:
        raise TpmAttestationError("unexpected public-key blob size")
    out = (ctypes.c_ubyte * size.value)()
    got = ctypes.c_ulong()
    _status(
        "NCryptExportKey",
        api.export_key(
            key, None, "RSAPUBLICBLOB", None, out, size.value, ctypes.byref(got), 0
        ),
    )
    return bytes(out[: got.value])


def _assert_identity_key(
    api: _NCrypt, provider: ctypes.c_void_p, key: ctypes.c_void_p
) -> dict[str, Any]:
    impl = _get_dword(api, provider, "Impl Type")
    export_policy = _get_dword(api, key, "Export Policy")
    usage = _get_dword(api, key, "PCP_KEY_USAGE_POLICY")
    if not impl & NCRYPT_IMPL_HARDWARE_FLAG:
        raise TpmAttestationError("identity key is not hardware-backed")
    if export_policy != 0:
        raise TpmAttestationError("identity key is exportable")
    if not usage & NCRYPT_PCP_IDENTITY_KEY:
        raise TpmAttestationError("key is not a PCP identity key")
    return {
        "hardware_backed": True,
        "private_key_exportable": False,
        "pcp_identity_key": True,
    }


def provision_identity_key(
    key_name: str = DEFAULT_KEY_NAME, *, overwrite: bool = False
) -> dict[str, Any]:
    """Create and validate a machine-scoped, non-exportable TPM identity key."""
    api = _NCrypt()
    provider = _open_provider(api)
    key = ctypes.c_void_p()
    flags = NCRYPT_MACHINE_KEY_FLAG | (NCRYPT_OVERWRITE_KEY_FLAG if overwrite else 0)
    try:
        _status(
            "NCryptCreatePersistedKey",
            api.create_key(provider, ctypes.byref(key), "RSA", key_name, 0, flags),
        )
        bits = ctypes.c_ulong(2048)
        _status(
            "NCryptSetProperty(Length)",
            api.set_property(key, "Length", ctypes.byref(bits), ctypes.sizeof(bits), 0),
        )
        usage = ctypes.c_ulong(NCRYPT_TPM12_PROVIDER | NCRYPT_PCP_IDENTITY_KEY)
        _status(
            "NCryptSetProperty(PCP_KEY_USAGE_POLICY)",
            api.set_property(
                key, "PCP_KEY_USAGE_POLICY", ctypes.byref(usage), ctypes.sizeof(usage), 0
            ),
        )
        _status("NCryptFinalizeKey", api.finalize_key(key, 0))
        properties = _assert_identity_key(api, provider, key)
        public_blob = _export_public_blob(api, key)
        return {
            "schema_version": SCHEMA_VERSION,
            "provider": PROVIDER,
            "key_name": key_name,
            "public_key_sha256": _sha256(public_blob),
            **properties,
        }
    except Exception:
        if key:
            api.delete_key(key, 0)
            key = ctypes.c_void_p()
        raise
    finally:
        if key:
            api.free_object(key)
        api.free_object(provider)


def _claim_parameters(nonce: bytes, pcr_mask: int) -> tuple[Any, _NCryptBufferDesc]:
    if not isinstance(nonce, bytes) or not 16 <= len(nonce) <= 64:
        raise ValueError("nonce must be 16..64 bytes")
    if not 0 < pcr_mask <= DEFAULT_PCR_MASK:
        raise ValueError("PCR mask must select PCR 0..23")
    nonce_buffer = ctypes.create_string_buffer(nonce)
    mask = ctypes.c_ulong(pcr_mask)
    buffers = (_NCryptBuffer * 2)(
        _NCryptBuffer(
            ctypes.sizeof(mask),
            NCRYPTBUFFER_TPM_PLATFORM_CLAIM_PCR_MASK,
            ctypes.cast(ctypes.byref(mask), ctypes.c_void_p),
        ),
        _NCryptBuffer(
            len(nonce),
            NCRYPTBUFFER_TPM_PLATFORM_CLAIM_NONCE,
            ctypes.cast(nonce_buffer, ctypes.c_void_p),
        ),
    )
    desc = _NCryptBufferDesc(0, len(buffers), buffers)
    return (nonce_buffer, mask, buffers), desc


def issue_platform_attestation(
    expected_nonce: bytes,
    *,
    key_name: str = DEFAULT_KEY_NAME,
    pcr_mask: int = DEFAULT_PCR_MASK,
) -> dict[str, Any]:
    """Issue a TPM quote bound to a verifier-provided nonce and PCR mask."""
    api = _NCrypt()
    with _open_key(api, key_name) as handles:
        properties = _assert_identity_key(api, handles.provider, handles.key)
        public_blob = _export_public_blob(api, handles.key)
        keepalive, params = _claim_parameters(expected_nonce, pcr_mask)
        _ = keepalive
        size = ctypes.c_ulong()
        _status(
            "NCryptCreateClaim(size)",
            api.create_claim(
                None,
                handles.key,
                NCRYPT_CLAIM_PLATFORM,
                ctypes.byref(params),
                None,
                0,
                ctypes.byref(size),
                0,
            ),
        )
        if size.value <= 24 or size.value > MAX_CLAIM_BYTES:
            raise TpmAttestationError("unexpected platform claim size")
        claim = (ctypes.c_ubyte * size.value)()
        got = ctypes.c_ulong()
        _status(
            "NCryptCreateClaim",
            api.create_claim(
                None,
                handles.key,
                NCRYPT_CLAIM_PLATFORM,
                ctypes.byref(params),
                claim,
                size.value,
                ctypes.byref(got),
                0,
            ),
        )
        claim_bytes = bytes(claim[: got.value])
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_type": "tpm-platform",
        "provider": PROVIDER,
        "key_name": key_name,
        "public_key_blob_b64u": _b64u(public_blob),
        "public_key_sha256": _sha256(public_blob),
        "claim_b64u": _b64u(claim_bytes),
        "claim_sha256": _sha256(claim_bytes),
        "nonce_sha256": _sha256(expected_nonce),
        "pcr_mask": pcr_mask,
        "issued_at": int(time.time()),
        **properties,
    }


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise TpmAttestationVerificationError("truncated TPM structure")
        out = self.data[self.pos : self.pos + size]
        self.pos += size
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def tpm2b(self) -> bytes:
        return self.take(self.u16())

    def done(self) -> None:
        if self.pos != len(self.data):
            raise TpmAttestationVerificationError("trailing TPM structure bytes")


@dataclass(frozen=True)
class _Quote:
    nonce: bytes
    selections: tuple[tuple[int, bytes], ...]
    pcr_digest: bytes


def _parse_quote(quote: bytes) -> _Quote:
    r = _Reader(quote)
    if r.u32() != TPM_GENERATED_VALUE or r.u16() != TPM_ST_ATTEST_QUOTE:
        raise TpmAttestationVerificationError("not a TPM quote")
    r.tpm2b()  # qualified signer
    nonce = r.tpm2b()
    r.take(8 + 4 + 4 + 1 + 8)  # clockInfo + firmwareVersion
    count = r.u32()
    if not 1 <= count <= 8:
        raise TpmAttestationVerificationError("invalid PCR selection count")
    selections: list[tuple[int, bytes]] = []
    for _ in range(count):
        alg = r.u16()
        select_size = r.u8()
        if not 1 <= select_size <= 4:
            raise TpmAttestationVerificationError("invalid PCR selection size")
        selections.append((alg, r.take(select_size)))
    pcr_digest = r.tpm2b()
    r.done()
    return _Quote(nonce, tuple(selections), pcr_digest)


def _parse_signature(signature: bytes) -> tuple[int, int, bytes]:
    r = _Reader(signature)
    scheme = r.u16()
    hash_alg = r.u16()
    sig = r.tpm2b()
    r.done()
    return scheme, hash_alg, sig


def _rsa_public_key(blob: bytes) -> rsa.RSAPublicKey:
    if len(blob) < 24:
        raise TpmAttestationVerificationError("truncated RSA public blob")
    magic, bit_length, cb_exp, cb_modulus, cb_p1, cb_p2 = struct.unpack("<6I", blob[:24])
    if magic != int.from_bytes(b"RSA1", "little") or cb_p1 or cb_p2:
        raise TpmAttestationVerificationError("invalid RSA public blob")
    if bit_length != cb_modulus * 8 or bit_length < 2048:
        raise TpmAttestationVerificationError("invalid RSA public-key size")
    if len(blob) != 24 + cb_exp + cb_modulus:
        raise TpmAttestationVerificationError("invalid RSA public-blob length")
    exponent = int.from_bytes(blob[24 : 24 + cb_exp], "big")
    modulus = int.from_bytes(blob[24 + cb_exp :], "big")
    try:
        return rsa.RSAPublicNumbers(exponent, modulus).public_key()
    except ValueError as exc:
        raise TpmAttestationVerificationError("invalid RSA public key") from exc


_HASHES: dict[int, type[hashes.HashAlgorithm]] = {
    TPM_ALG_SHA1: hashes.SHA1,
    TPM_ALG_SHA256: hashes.SHA256,
    TPM_ALG_SHA384: hashes.SHA384,
    TPM_ALG_SHA512: hashes.SHA512,
}


def _mask_bytes(pcr_mask: int, size: int) -> bytes:
    return int(pcr_mask).to_bytes(size, "little")


def _selected_pcr_digest(
    pcrs: bytes, *, pcr_alg: int, digest_alg: int, mask: bytes
) -> bytes:
    pcr_hash_type = _HASHES.get(pcr_alg)
    digest_hash_type = _HASHES.get(digest_alg)
    if pcr_hash_type is None or digest_hash_type is None:
        raise TpmAttestationVerificationError("unsupported PCR hash algorithm")
    digest_size = pcr_hash_type().digest_size
    if len(pcrs) != 24 * digest_size:
        raise TpmAttestationVerificationError("unexpected PCR value inventory")
    selected = b"".join(
        pcrs[index * digest_size : (index + 1) * digest_size]
        for index in range(24)
        if mask[index // 8] & (1 << (index % 8))
    )
    digest = hashes.Hash(digest_hash_type())
    digest.update(selected)
    return digest.finalize()


def _strict_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "claim_type",
        "provider",
        "key_name",
        "public_key_blob_b64u",
        "public_key_sha256",
        "claim_b64u",
        "claim_sha256",
        "nonce_sha256",
        "pcr_mask",
        "issued_at",
        "hardware_backed",
        "private_key_exportable",
        "pcp_identity_key",
    }
    if type(artifact) is not dict or set(artifact) != required:
        raise TpmAttestationVerificationError("invalid attestation artifact shape")
    if artifact["schema_version"] != SCHEMA_VERSION or artifact["claim_type"] != "tpm-platform":
        raise TpmAttestationVerificationError("unsupported attestation artifact")
    if artifact["provider"] != PROVIDER:
        raise TpmAttestationVerificationError("unexpected key provider")
    if artifact["hardware_backed"] is not True or artifact["private_key_exportable"] is not False:
        raise TpmAttestationVerificationError("artifact does not assert a hardware non-exportable key")
    if artifact["pcp_identity_key"] is not True:
        raise TpmAttestationVerificationError("artifact does not assert a PCP identity key")
    if type(artifact["pcr_mask"]) is not int or not 0 < artifact["pcr_mask"] <= DEFAULT_PCR_MASK:
        raise TpmAttestationVerificationError("invalid PCR mask")
    if type(artifact["issued_at"]) is not int or artifact["issued_at"] <= 0:
        raise TpmAttestationVerificationError("invalid issuance time")
    return dict(artifact)


def verify_platform_attestation(
    artifact: dict[str, Any],
    expected_nonce: bytes,
    *,
    expected_public_key_sha256: str,
    expected_pcr_mask: int = DEFAULT_PCR_MASK,
) -> dict[str, Any]:
    """Cryptographically verify a platform quote against verifier policy."""
    item = _strict_artifact(artifact)
    if not isinstance(expected_nonce, bytes) or not 16 <= len(expected_nonce) <= 64:
        raise ValueError("expected nonce must be 16..64 bytes")
    if item["nonce_sha256"] != _sha256(expected_nonce):
        raise TpmAttestationVerificationError("nonce metadata mismatch")
    if item["pcr_mask"] != expected_pcr_mask:
        raise TpmAttestationVerificationError("PCR mask policy mismatch")
    public_blob = _unb64u(item["public_key_blob_b64u"])
    claim = _unb64u(item["claim_b64u"])
    if len(claim) > MAX_CLAIM_BYTES or _sha256(claim) != item["claim_sha256"]:
        raise TpmAttestationVerificationError("claim digest mismatch")
    public_digest = _sha256(public_blob)
    if public_digest != item["public_key_sha256"] or public_digest != expected_public_key_sha256:
        raise TpmAttestationVerificationError("attestation key is not pinned")
    if len(claim) < 24 or claim[:4] != _PLATFORM_MAGIC:
        raise TpmAttestationVerificationError("invalid platform claim header")
    _magic, version, pcr_alg, cb_signature, cb_quote, cb_pcrs = struct.unpack(
        "<6I", claim[:24]
    )
    if version != 0 or any(size <= 0 for size in (cb_signature, cb_quote, cb_pcrs)):
        raise TpmAttestationVerificationError("invalid platform claim version or sizes")
    if 24 + cb_signature + cb_quote + cb_pcrs != len(claim):
        raise TpmAttestationVerificationError("platform claim length mismatch")
    signature = claim[24 : 24 + cb_signature]
    quote = claim[24 + cb_signature : 24 + cb_signature + cb_quote]
    pcrs = claim[-cb_pcrs:]
    parsed = _parse_quote(quote)
    scheme, signature_hash, signature_bytes = _parse_signature(signature)
    hash_type = _HASHES.get(signature_hash)
    if scheme != TPM_ALG_RSASSA or hash_type is None:
        raise TpmAttestationVerificationError("unsupported TPM quote signature")
    if parsed.nonce != expected_nonce:
        raise TpmAttestationVerificationError("TPM quote nonce mismatch")
    expected_mask = _mask_bytes(expected_pcr_mask, 3)
    if parsed.selections != ((pcr_alg, expected_mask),):
        raise TpmAttestationVerificationError("TPM quote PCR selection mismatch")
    if parsed.pcr_digest != _selected_pcr_digest(
        pcrs, pcr_alg=pcr_alg, digest_alg=signature_hash, mask=expected_mask
    ):
        raise TpmAttestationVerificationError("TPM quote PCR digest mismatch")
    try:
        _rsa_public_key(public_blob).verify(
            signature_bytes, quote, padding.PKCS1v15(), hash_type()
        )
    except Exception as exc:
        raise TpmAttestationVerificationError("TPM quote signature verification failed") from exc
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "claim_sha256": item["claim_sha256"],
        "public_key_sha256": public_digest,
        "nonce_sha256": item["nonce_sha256"],
        "pcr_mask": expected_pcr_mask,
        "pcr_algorithm": pcr_alg,
        "pcr_values_sha256": _sha256(pcrs),
        "manufacturer_chain_verified": False,
        "raw_claim_included": False,
    }


class NonceReplayStore:
    """Durable single-use verifier nonce store with a cross-process file lock."""

    def __init__(self, path: str | Path, *, retention_s: int = 86400) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        if retention_s < 60:
            raise ValueError("retention_s must be at least 60")
        self.retention_s = int(retention_s)

    def consume(self, nonce: bytes, *, now: int | None = None) -> str:
        if not isinstance(nonce, bytes) or not 16 <= len(nonce) <= 64:
            raise ValueError("nonce must be 16..64 bytes")
        moment = int(time.time()) if now is None else int(now)
        digest = _sha256(nonce)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.lock_path, timeout=5.0):
            data: dict[str, int] = {}
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if type(loaded) is not dict or any(
                        type(k) is not str or type(v) is not int for k, v in loaded.items()
                    ):
                        raise ValueError
                    data = loaded
                except Exception as exc:
                    raise TpmAttestationReplayError("nonce replay store is corrupt") from exc
            cutoff = moment - self.retention_s
            data = {key: seen for key, seen in data.items() if seen >= cutoff}
            if digest in data:
                raise TpmAttestationReplayError("attestation nonce was already consumed")
            data[digest] = moment
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(data, handle, sort_keys=True, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
        return digest


def verify_and_consume(
    artifact: dict[str, Any],
    expected_nonce: bytes,
    *,
    expected_public_key_sha256: str,
    replay_store: NonceReplayStore,
    expected_pcr_mask: int = DEFAULT_PCR_MASK,
) -> dict[str, Any]:
    result = verify_platform_attestation(
        artifact,
        expected_nonce,
        expected_public_key_sha256=expected_public_key_sha256,
        expected_pcr_mask=expected_pcr_mask,
    )
    replay_store.consume(expected_nonce)
    result["replay_checked"] = True
    return result


def hardware_selftest(*, key_name: str | None = None) -> dict[str, Any]:
    """Provision an ephemeral TPM identity key and exercise positive/negative paths."""
    name = key_name or f"SelfConnectPlatformAIK-Selftest-{os.getpid()}-{time.time_ns()}"
    api = _NCrypt()
    created = False
    try:
        provisioned = provision_identity_key(name)
        created = True
        nonce = os.urandom(32)
        artifact = issue_platform_attestation(nonce, key_name=name)
        verified = verify_platform_attestation(
            artifact,
            nonce,
            expected_public_key_sha256=provisioned["public_key_sha256"],
        )
        nonce_rejected = False
        try:
            verify_platform_attestation(
                artifact,
                bytes([nonce[0] ^ 1]) + nonce[1:],
                expected_public_key_sha256=provisioned["public_key_sha256"],
            )
        except TpmAttestationVerificationError:
            nonce_rejected = True
        tampered = dict(artifact)
        raw = bytearray(_unb64u(tampered["claim_b64u"]))
        raw[-1] ^= 1
        tampered["claim_b64u"] = _b64u(bytes(raw))
        tampered["claim_sha256"] = _sha256(bytes(raw))
        tamper_rejected = False
        try:
            verify_platform_attestation(
                tampered,
                nonce,
                expected_public_key_sha256=provisioned["public_key_sha256"],
            )
        except TpmAttestationVerificationError:
            tamper_rejected = True
        return {
            "ok": bool(verified["ok"] and nonce_rejected and tamper_rejected),
            "schema_version": SCHEMA_VERSION,
            "provider": PROVIDER,
            "hardware_backed": provisioned["hardware_backed"],
            "private_key_exportable": provisioned["private_key_exportable"],
            "quote_verified": verified["ok"],
            "nonce_mismatch_rejected": nonce_rejected,
            "tampered_pcr_rejected": tamper_rejected,
            "public_key_sha256": provisioned["public_key_sha256"],
            "manufacturer_chain_verified": False,
            "raw_claim_included": False,
        }
    finally:
        if created:
            try:
                with _open_key(api, name) as handles:
                    _status("NCryptDeleteKey", api.delete_key(handles.key, 0))
                    handles.key = ctypes.c_void_p()
            except Exception:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SelfConnect TPM platform attestation")
    sub = parser.add_subparsers(dest="command", required=True)
    p_provision = sub.add_parser("provision")
    p_provision.add_argument("--key-name", default=DEFAULT_KEY_NAME)
    p_provision.add_argument("--overwrite", action="store_true")
    p_selftest = sub.add_parser("selftest")
    p_selftest.add_argument("--output", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "provision":
        result = provision_identity_key(args.key_name, overwrite=args.overwrite)
    else:
        result = hardware_selftest()
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
