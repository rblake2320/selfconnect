"""
vault.py — Local encrypted secrets vault for SelfConnect agents.

Stores secrets encrypted with a key derived from a passphrase (PBKDF2 + AES-256-GCM).
Key is cached in-process only — never written to disk.
Secrets file is encrypted JSON — safe to commit to private repos.

IMPORTANT: This is a LOCAL vault for development/personal use. For production
IL2/IL4 environments, use Windows DPAPI or a proper secrets manager (Vault, AWS SSM).

Usage:
    from vault import Vault

    v = Vault("~/.sc_vault")
    v.unlock("my-passphrase")
    token = v.get("frp_jwt")
    v.set("frp_jwt", "eyJ...")
    v.lock()
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import secrets
import tempfile
import warnings
from typing import Any

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


class VaultError(Exception):
    """Base exception for vault operations."""


class VaultLocked(VaultError):
    """Raised when attempting to access secrets while vault is locked."""


class Vault:
    """Local encrypted secrets vault with passphrase-based key derivation."""

    _PBKDF2_ITERATIONS = 300_000
    _SALT_BYTES = 32
    _KEY_BYTES = 32
    _NONCE_BYTES = 12

    def __init__(self, vault_path: str | os.PathLike = "~/.sc_vault") -> None:
        self._path = os.path.abspath(os.path.expanduser(str(vault_path)))
        self._key: bytes | None = None
        self._secrets: dict[str, str] = {}
        self._salt: bytes | None = None
        self._key_names: list[str] = []
        # Load unencrypted metadata (key names) if file exists
        self._load_metadata()

    def unlock(self, passphrase: str | None = None) -> None:
        """Unlock the vault. Prompts for passphrase if not provided."""
        if passphrase is None:
            passphrase = getpass.getpass("Vault passphrase: ")

        if not _CRYPTO_AVAILABLE:
            warnings.warn(
                "cryptography library not installed. "
                "Using base64-XOR fallback — NOT SECURE. "
                "Install cryptography: pip install cryptography",
                UserWarning,
                stacklevel=2,
            )

        if not os.path.exists(self._path):
            # New vault — create salt, empty secrets
            self._salt = secrets.token_bytes(self._SALT_BYTES)
            self._key = self._derive_key(passphrase, self._salt)
            self._secrets = {}
            self._key_names = []
            self._save()
            return

        # Existing vault — load and decrypt
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)

        self._salt = base64.b64decode(data["salt"])
        self._key = self._derive_key(passphrase, self._salt)
        self._key_names = data.get("keys", [])

        encrypted_b64 = data.get("secrets", "")
        if not encrypted_b64:
            self._secrets = {}
            return

        encrypted_raw = base64.b64decode(encrypted_b64)
        try:
            plaintext = self._decrypt_bytes(encrypted_raw)
        except Exception as exc:
            self._key = None
            raise VaultError(f"Wrong passphrase or corrupted vault: {exc}") from exc

        self._secrets = json.loads(plaintext)

    def lock(self) -> None:
        """Clear in-memory key and secrets."""
        self._key = None
        self._secrets = {}

    def is_locked(self) -> bool:
        """True if vault key is not loaded."""
        return self._key is None

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get a secret value. Raises VaultLocked if locked."""
        if self.is_locked():
            raise VaultLocked("Vault is locked — call unlock() first")
        return self._secrets.get(key, default)

    def set(self, key: str, value: str) -> None:
        """Set a secret and immediately save encrypted."""
        if self.is_locked():
            raise VaultLocked("Vault is locked — call unlock() first")
        self._secrets[key] = value
        if key not in self._key_names:
            self._key_names.append(key)
        self._save()

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if it existed."""
        if self.is_locked():
            raise VaultLocked("Vault is locked — call unlock() first")
        existed = key in self._secrets
        self._secrets.pop(key, None)
        if key in self._key_names:
            self._key_names.remove(key)
        self._save()
        return existed

    def keys(self) -> list[str]:
        """Return key names (not values). Works even when locked."""
        return list(self._key_names)

    # -- context manager --

    def __enter__(self) -> Vault:
        if self.is_locked():
            self.unlock()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.lock()

    # -- crypto internals --

    def _derive_key(self, passphrase: str, salt: bytes) -> bytes:
        """PBKDF2HMAC SHA-256, 300k iterations, returns 32-byte key."""
        if _CRYPTO_AVAILABLE:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=self._KEY_BYTES,
                salt=salt,
                iterations=self._PBKDF2_ITERATIONS,
            )
            return kdf.derive(passphrase.encode("utf-8"))
        # Fallback: stdlib hashlib pbkdf2_hmac
        return hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode("utf-8"), salt,
            self._PBKDF2_ITERATIONS, dklen=self._KEY_BYTES,
        )

    def _encrypt_bytes(self, plaintext: str) -> bytes:
        """Encrypt plaintext string, return nonce + ciphertext blob."""
        data = plaintext.encode("utf-8")
        if _CRYPTO_AVAILABLE:
            assert self._key is not None
            nonce = secrets.token_bytes(self._NONCE_BYTES)
            aesgcm = AESGCM(self._key)
            ct = aesgcm.encrypt(nonce, data, None)
            return nonce + ct
        # XOR fallback (NOT secure — dev only)
        return self._xor_bytes(data)

    def _decrypt_bytes(self, blob: bytes) -> str:
        """Decrypt blob back to plaintext string."""
        if _CRYPTO_AVAILABLE:
            assert self._key is not None
            nonce = blob[: self._NONCE_BYTES]
            ct = blob[self._NONCE_BYTES :]
            aesgcm = AESGCM(self._key)
            plaintext = aesgcm.decrypt(nonce, ct, None)
            return plaintext.decode("utf-8")
        # XOR fallback
        return self._xor_bytes(blob).decode("utf-8")

    def _xor_bytes(self, data: bytes) -> bytes:
        """XOR data with repeating key. Symmetric — encrypt == decrypt."""
        assert self._key is not None
        key = self._key
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

    # -- persistence --

    def _save(self) -> None:
        """Atomically write vault file."""
        assert self._key is not None
        assert self._salt is not None

        plaintext_json = json.dumps(self._secrets)
        encrypted = self._encrypt_bytes(plaintext_json)

        data = {
            "salt": base64.b64encode(self._salt).decode("ascii"),
            "keys": list(self._key_names),
            "secrets": base64.b64encode(encrypted).decode("ascii"),
        }

        vault_dir = os.path.dirname(self._path)
        if vault_dir:
            os.makedirs(vault_dir, exist_ok=True)

        fd, tmp = tempfile.mkstemp(
            dir=vault_dir or ".", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            try:
                os.remove(self._path)
            except FileNotFoundError:
                pass
            os.rename(tmp, self._path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def _load_metadata(self) -> None:
        """Load just the unencrypted key names from vault file."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._key_names = data.get("keys", [])
        except (json.JSONDecodeError, OSError):
            pass
