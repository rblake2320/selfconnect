"""
mesh_scrubber.py — Pre-injection secrets sanitizer for SelfConnect mesh messages.

Scrubs known credential patterns from any string before it is injected into a
peer terminal via send_string(). This is a defense-in-depth layer: agents should
never put secrets in mesh messages, but if they do, this catches it.

Usage:
    from mesh_scrubber import scrub, ScrubResult

    result = scrub(message)
    if result.scrubbed:
        print(f"WARNING: {result.hit_count} secret(s) redacted from mesh message")
    safe_message = result.text
    send_string(peer_win, safe_message + "\\r")

Or use the one-liner:
    safe = scrub(message).text

Public API:
    scrub(text: str) -> ScrubResult
    is_clean(text: str) -> bool
    ScrubResult(text, scrubbed, hit_count, hits)
"""

from __future__ import annotations

import dataclasses
import logging
import re

__all__ = ["PATTERNS", "ScrubResult", "is_clean", "scrub"]

_log = logging.getLogger(__name__)

# ── Secret patterns ──────────────────────────────────────────────────────────
# Each pattern is (name, compiled_regex, replacement). Groups must not capture
# partial secrets — the entire match is replaced.

_PATTERN_DEFS: list[tuple[str, str, str]] = [
    # Bearer / Authorization tokens (JWT format: xxx.yyy.zzz)
    (
        "bearer_token",
        r"\bBearer\s+[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_=]+",
        "[BEARER_TOKEN_REDACTED]",
    ),
    # Raw JWTs (three base64url segments separated by dots)
    (
        "jwt",
        r"\b[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_=]{20,}\b",
        "[JWT_REDACTED]",
    ),
    # Generic API keys — common prefixes
    (
        "api_key_prefix",
        r"\b(?:sk|pk|rk|ak|api|key|tok|auth)[-_][A-Za-z0-9\-_]{16,}\b",
        "[API_KEY_REDACTED]",
    ),
    # AWS access key IDs
    (
        "aws_access_key",
        r"\bAKIA[0-9A-Z]{16}\b",
        "[AWS_KEY_REDACTED]",
    ),
    # AWS secret access keys (40 chars, base62)
    (
        "aws_secret",
        r"(?i)aws.{0,20}secret.{0,20}['\"]?([A-Za-z0-9/+=]{40})['\"]?",
        "[AWS_SECRET_REDACTED]",
    ),
    # GitHub PATs (classic and fine-grained)
    (
        "github_pat",
        r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
        "[GITHUB_TOKEN_REDACTED]",
    ),
    # Cloudflare API tokens
    (
        "cf_token",
        r"\b[A-Za-z0-9_-]{40}\b(?=.*cloudflare)",  # lookahead — only near 'cloudflare'
        "[CF_TOKEN_REDACTED]",
    ),
    # PEM private keys (inline)
    (
        "pem_private",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "[PRIVATE_KEY_REDACTED]",
    ),
    # Authorization header values (non-bearer)
    (
        "auth_header",
        r"(?i)authorization\s*:\s*\S{10,}",
        "[AUTH_HEADER_REDACTED]",
    ),
    # Password fields in URL or query string
    (
        "url_password",
        r"(?i)(?:password|passwd|pwd|secret)=\S{6,}",
        "[PASSWORD_REDACTED]",
    ),
    # Generic long hex strings that look like secrets (≥32 hex chars)
    # Only match when adjacent to a keyword suggesting it's a secret
    (
        "hex_secret",
        r"(?i)(?:token|secret|key|hash|digest|sig|hmac)\s*[=:]\s*[0-9a-f]{32,}",
        "[HEX_SECRET_REDACTED]",
    ),
]

# Compile once at import time
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (name, re.compile(pattern, re.DOTALL), replacement)
    for name, pattern, replacement in _PATTERN_DEFS
]


# ── Result type ───────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ScrubResult:
    """Result of a scrub() call."""

    text: str
    """The sanitized text — safe to inject into a peer terminal."""

    scrubbed: bool
    """True if at least one secret was found and redacted."""

    hit_count: int
    """Number of redactions made."""

    hits: tuple[str, ...]
    """Names of the pattern types that matched (may contain duplicates)."""

    def __bool__(self) -> bool:
        """True means the message is clean (no secrets found)."""
        return not self.scrubbed


# ── Core functions ────────────────────────────────────────────────────────────


def scrub(text: str) -> ScrubResult:
    """
    Scan text for credential patterns and redact any matches.

    Returns a ScrubResult. The .text field is always safe to use.
    NEVER raises — if something goes wrong, returns the original text unmodified
    with scrubbed=False so the caller can decide what to do.
    """
    if not text:
        return ScrubResult(text=text, scrubbed=False, hit_count=0, hits=())

    try:
        result = text
        total_hits = 0
        hit_names: list[str] = []

        for name, pattern, replacement in PATTERNS:
            new_result, count = pattern.subn(replacement, result)
            if count:
                total_hits += count
                hit_names.extend([name] * count)
                result = new_result

        if total_hits:
            _log.warning(
                "mesh_scrubber: %d secret(s) redacted from mesh message (%s)",
                total_hits,
                ", ".join(dict.fromkeys(hit_names)),  # unique names, order preserved
            )

        return ScrubResult(
            text=result,
            scrubbed=total_hits > 0,
            hit_count=total_hits,
            hits=tuple(hit_names),
        )
    except Exception as exc:
        _log.error("mesh_scrubber: unexpected error during scrub: %s", exc)
        # Safe fallback — return original, caller must decide
        return ScrubResult(text=text, scrubbed=False, hit_count=0, hits=())


def is_clean(text: str) -> bool:
    """
    Return True if no credential patterns are found in text.

    Convenience wrapper around scrub(). Use scrub() if you need the cleaned text.
    """
    return not scrub(text).scrubbed
