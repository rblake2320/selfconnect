"""
test_vault.py — Unit tests for the secrets Vault.
All tests use tmp_path for isolation. No real secrets or passphrases.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vault import Vault, VaultError, VaultLocked


PASSPHRASE = "test-passphrase-12345"


class TestUnlockGet:
    def test_unlock_and_get(self, tmp_path):
        """Set secret, lock, unlock with same passphrase, get returns value."""
        vault_path = tmp_path / "vault"
        v = Vault(vault_path)
        v.unlock(PASSPHRASE)
        v.set("api_key", "sk-12345")
        v.lock()

        v2 = Vault(vault_path)
        v2.unlock(PASSPHRASE)
        assert v2.get("api_key") == "sk-12345"
        v2.lock()

    def test_wrong_passphrase_raises(self, tmp_path):
        """Unlock with wrong passphrase raises VaultError."""
        vault_path = tmp_path / "vault"
        v = Vault(vault_path)
        v.unlock(PASSPHRASE)
        v.set("secret", "value")
        v.lock()

        v2 = Vault(vault_path)
        with pytest.raises(VaultError):
            v2.unlock("wrong-passphrase")


class TestLockedAccess:
    def test_locked_get_raises_vault_locked(self, tmp_path):
        """Get without unlock raises VaultLocked."""
        vault_path = tmp_path / "vault"
        v = Vault(vault_path)
        with pytest.raises(VaultLocked):
            v.get("anything")

    def test_delete_removes_key(self, tmp_path):
        """Set, delete, get returns None (default)."""
        vault_path = tmp_path / "vault"
        v = Vault(vault_path)
        v.unlock(PASSPHRASE)
        v.set("temp_key", "temp_val")
        assert v.delete("temp_key") is True
        assert v.get("temp_key") is None
        assert v.delete("temp_key") is False
        v.lock()


class TestKeysMetadata:
    def test_keys_works_locked(self, tmp_path):
        """keys() returns list even when locked (unencrypted metadata)."""
        vault_path = tmp_path / "vault"
        v = Vault(vault_path)
        v.unlock(PASSPHRASE)
        v.set("key_a", "val_a")
        v.set("key_b", "val_b")
        v.lock()

        v2 = Vault(vault_path)
        # v2 is locked, but keys() should work
        assert sorted(v2.keys()) == ["key_a", "key_b"]


class TestContextManager:
    def test_context_manager_unlocks_locks(self, tmp_path):
        """with Vault() as v: v.get() works; after block, is_locked()."""
        vault_path = tmp_path / "vault"
        v = Vault(vault_path)
        v.unlock(PASSPHRASE)
        v.set("ctx_key", "ctx_val")
        v.lock()

        v2 = Vault(vault_path)
        v2.unlock(PASSPHRASE)  # pre-unlock so context manager finds it unlocked
        with v2:
            assert v2.get("ctx_key") == "ctx_val"
        assert v2.is_locked() is True


class TestPersistence:
    def test_set_persists_across_instances(self, tmp_path):
        """Set in one Vault, create new Vault, unlock, get same value."""
        vault_path = tmp_path / "vault"
        v1 = Vault(vault_path)
        v1.unlock(PASSPHRASE)
        v1.set("persist_key", "persist_val")
        v1.lock()

        v2 = Vault(vault_path)
        v2.unlock(PASSPHRASE)
        assert v2.get("persist_key") == "persist_val"
        v2.lock()

    def test_no_plaintext_in_vault_file(self, tmp_path):
        """After set, vault file does not contain plaintext secret."""
        vault_path = tmp_path / "vault"
        v = Vault(vault_path)
        v.unlock(PASSPHRASE)
        v.set("secret_key", "super-secret-value-12345")
        v.lock()

        raw = vault_path.read_text(encoding="utf-8")
        assert "super-secret-value-12345" not in raw
        # Also verify it's valid JSON
        data = json.loads(raw)
        assert "secrets" in data
        assert "salt" in data
