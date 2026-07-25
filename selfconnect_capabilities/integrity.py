"""Operating-system protected keys and authenticated canonical digests."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from sc_tasks import FileLock


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


class IntegrityKey:
    """A stable local key protected by Windows DPAPI when available."""

    def __init__(self, root: Path):
        self.path = root / ".integrity_key.dpapi"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key = self._load_or_create()

    def digest(self, value: Any) -> str:
        return hmac.new(self.key, canonical_bytes(value), hashlib.sha256).hexdigest()

    def _load_or_create(self) -> bytes:
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with FileLock(lock):
            if self.path.exists():
                return self._unprotect(self.path.read_bytes())
            key = os.urandom(32)
            protected = self._protect(key)
            temp = self.path.with_suffix(f".{os.getpid()}.tmp")
            temp.write_bytes(protected)
            os.replace(temp, self.path)
            return key

    @staticmethod
    def _protect(value: bytes) -> bytes:
        if os.name == "nt":
            import win32crypt

            return win32crypt.CryptProtectData(
                value,
                "SelfConnect capability integrity key",
                None,
                None,
                None,
                0,
            )
        return value

    @staticmethod
    def _unprotect(value: bytes) -> bytes:
        if os.name == "nt":
            import win32crypt

            return bytes(win32crypt.CryptUnprotectData(value, None, None, None, 0)[1])
        return value
