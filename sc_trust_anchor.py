"""Local integrity journals and the high-assurance trust-boundary gate.

The journal in this module is deliberately *not* described as monotonic
security.  Windows DPAPI with current-user scope protects confidentiality from
other users, but a hostile process running as the same user can copy, delete,
or restore both the state file and its DPAPI-protected journal.  The journal is
therefore useful for corruption and accidental single-file rollback detection
only.

SelfConnect's high-assurance trust path requires a separately privileged
anchor service.  This package does not silently substitute current-user DPAPI
when that boundary has not been provisioned; high-assurance operations fail
closed with :class:`HighAssuranceUnavailable`.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any, ClassVar

ANCHOR_SCHEMA = "selfconnect-local-integrity-anchor-v1"


class HighAssuranceUnavailable(RuntimeError):
    """Raised when no separately privileged trust-anchor service is available."""


def require_high_assurance_anchor() -> None:
    """Fail closed until a separately privileged anchor service is provisioned.

    A same-user file, ACL, environment variable, Python object, or DPAPI blob is
    intentionally insufficient for this threat model.  A future implementation
    must authenticate a service running under a distinct Windows principal (or
    an equivalent hardware/remote boundary) and obtain the monotonic head from
    that boundary.
    """
    raise HighAssuranceUnavailable(
        "high-assurance trust is unavailable: provision a separately privileged "
        "trust-anchor service; current-user DPAPI is local integrity only"
    )


class _DATA_BLOB(ctypes.Structure):
    _fields_: ClassVar = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "SelfConnect local integrity journal",
            None,
            None,
            None,
            0x1,
            ctypes.byref(output),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(output),
        )
    if not ok:
        raise OSError(f"DPAPI anchor operation failed ({ctypes.windll.kernel32.GetLastError()})")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _anchor_path(state_path: str | Path) -> Path:
    path = Path(state_path).resolve()
    return path.with_suffix(path.suffix + ".local-integrity-anchor")


def _body(namespace: str, epoch: int, version: int, digest: str) -> dict[str, Any]:
    if not namespace or type(epoch) is not int or type(version) is not int:
        raise ValueError("local integrity anchor identity is invalid")
    if epoch < 1 or version < 1 or len(digest) != 64:
        raise ValueError("local integrity anchor counters or digest are invalid")
    bytes.fromhex(digest)
    return {
        "schema": ANCHOR_SCHEMA,
        "namespace": namespace,
        "epoch": epoch,
        "version": version,
        "state_sha256": digest,
    }


def _decode_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    if os.name == "nt":
        raw = _dpapi(base64.b64decode(raw, validate=True), protect=False)
    document = json.loads(raw)
    records = document.get("records")
    if document.get("schema") != ANCHOR_SCHEMA or not isinstance(records, list) or not records:
        raise ValueError("local integrity anchor is malformed")
    previous = "0" * 64
    for record in records:
        body = record.get("body")
        if not isinstance(body, dict) or record.get("previous_sha256") != previous:
            raise ValueError("local integrity anchor chain is invalid")
        expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if record.get("record_sha256") != expected:
            raise ValueError("local integrity anchor record digest is invalid")
        previous = expected
    return records


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    raw = json.dumps(
        {"schema": ANCHOR_SCHEMA, "records": records},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if os.name == "nt":
        raw = base64.b64encode(_dpapi(raw, protect=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_bytes(raw)
    os.chmod(staged, 0o600)
    os.replace(staged, path)
    if os.name == "nt":
        from sc_guarded_submit import _protect_evidence_path

        _protect_evidence_path(path)


def advance_local_integrity_anchor(
    state_path: str | Path,
    namespace: str,
    epoch: int,
    version: int,
    digest: str,
) -> None:
    path = _anchor_path(state_path)
    records = _decode_records(path)
    body = _body(namespace, epoch, version, digest)
    if records:
        prior = records[-1]["body"]
        if prior["namespace"] != namespace:
            raise ValueError("local integrity anchor namespace changed")
        if (epoch, version) <= (prior["epoch"], prior["version"]):
            if body == prior:
                return
            raise ValueError("local integrity anchor rollback or replay rejected")
        previous = records[-1]["record_sha256"]
    else:
        previous = "0" * 64
    record_sha256 = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    records.append({"body": body, "previous_sha256": previous, "record_sha256": record_sha256})
    _write_records(path, records)


def verify_local_integrity_anchor(
    state_path: str | Path,
    namespace: str,
    epoch: int,
    version: int,
    digest: str,
) -> None:
    records = _decode_records(_anchor_path(state_path))
    if not records:
        raise ValueError("required local integrity anchor is absent")
    if records[-1]["body"] != _body(namespace, epoch, version, digest):
        raise ValueError("state rollback detected by local integrity anchor")
