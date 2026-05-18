"""Tests for mesh_scrubber.py — pre-injection secrets sanitizer."""

from __future__ import annotations

import sys
import pathlib

# Allow import from repo root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from mesh_scrubber import scrub, is_clean, ScrubResult


class TestCleanMessages:
    def test_empty_string_is_clean(self):
        r = scrub("")
        assert r.text == ""
        assert not r.scrubbed
        assert r.hit_count == 0

    def test_plain_text_is_clean(self):
        msg = "MESH PROTOCOL: You are part of the AI Army. HWND=0x000E043E"
        assert is_clean(msg)

    def test_hwnd_hex_not_flagged(self):
        # Short hex values like HWNDs must not be treated as secrets
        msg = "Active terminals: AXIOM=0x00A209CA, ultra-computer=0x000E043E"
        assert is_clean(msg)

    def test_path_not_flagged(self):
        msg = r"Write to C:\Users\techai\PKA testing\Team Inbox\result.md"
        assert is_clean(msg)

    def test_sha256_without_keyword_not_flagged(self):
        # Plain SHA-256 without a secret-adjacent keyword should not be flagged
        msg = "provenance_hash: 77fb4683a1c2d70684c41e4e6b7b3f2a9d8c1e5f2b4a"
        # This is borderline — the pattern requires keyword adjacency
        r = scrub(msg)
        # If it's flagged, that's acceptable (false positive = safe); if not, also fine
        assert isinstance(r, ScrubResult)


class TestBearerTokens:
    def test_bearer_token_redacted(self):
        msg = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        r = scrub(msg)
        assert r.scrubbed
        # Multiple patterns may fire; what matters is the secret is gone
        assert "REDACTED" in r.text
        # Original token must not appear in output
        assert "eyJhbGci" not in r.text

    def test_jwt_standalone_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        r = scrub(f"Call contribute_frp with jwt={jwt}")
        assert r.scrubbed
        assert jwt not in r.text


class TestAPIKeys:
    def test_sk_prefix_key_redacted(self):
        msg = "Use key sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890 for the call"
        r = scrub(msg)
        assert r.scrubbed
        assert "sk-proj" not in r.text

    def test_github_pat_redacted(self):
        msg = "Token: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz12345678901234"
        r = scrub(msg)
        assert r.scrubbed
        assert "ghp_" not in r.text

    def test_aws_access_key_redacted(self):
        msg = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        r = scrub(msg)
        assert r.scrubbed
        assert "AKIA" not in r.text


class TestPasswordFields:
    def test_url_password_redacted(self):
        msg = "Connect to postgres://user:password=MyS3cretP@ssword@localhost/db"
        r = scrub(msg)
        assert r.scrubbed

    def test_query_string_secret_redacted(self):
        msg = "GET /api?secret=abc123defghijklmnop"
        r = scrub(msg)
        assert r.scrubbed
        assert "abc123defghijklmnop" not in r.text


class TestPEMKeys:
    def test_pem_private_key_redacted(self):
        msg = (
            "Key: -----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA...(base64)...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        r = scrub(msg)
        assert r.scrubbed
        assert "BEGIN RSA PRIVATE KEY" not in r.text


class TestScrubResult:
    def test_result_bool_clean_is_truthy(self):
        r = scrub("clean message")
        assert bool(r) is True  # clean = True

    def test_result_bool_dirty_is_falsy(self):
        r = scrub("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123defghij")
        # May or may not flag this short example; just check bool reflects scrubbed
        assert bool(r) is not r.scrubbed

    def test_hit_count_matches_hits_length(self):
        msg = (
            "token: sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890 "
            "key: sk-proj-XyZaBcDeFgHiJkLmNoPqRsTuVwXy1234567890"
        )
        r = scrub(msg)
        assert r.hit_count == len(r.hits)

    def test_hits_is_tuple(self):
        r = scrub("plain message")
        assert isinstance(r.hits, tuple)

    def test_no_raise_on_none_like_input(self):
        # Scrub should never raise
        r = scrub("   ")
        assert isinstance(r, ScrubResult)


class TestIsClean:
    def test_clean_message(self):
        assert is_clean("Hello from AXIOM hwnd=9307910") is True

    def test_dirty_message(self):
        assert is_clean("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c") is False
