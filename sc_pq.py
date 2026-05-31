"""
sc_pq.py — SelfConnect Post-Quantum Cryptography Upgrade  (v1.0.0)

Fills the gap identified in the 2026 agentic security analysis:
  "TSK uses Ed25519 — harvest-now-decrypt-later attacks are a real threat
   for long-lived agent identities and delegation chains."

NIST finalised ML-DSA (FIPS 204, formerly CRYSTALS-Dilithium) in August 2024.
NSA CNSA 2.0 mandates ML-DSA for new systems by 2030 and existing systems
by 2035. For DoD/Five Eyes IL4-IL7 compliance, hybrid classical+PQ signing
is the correct migration path: both signatures must verify for the message
to be accepted — this provides:
  - Security against classical adversaries (Ed25519 still works)
  - Security against quantum adversaries (ML-DSA is quantum-resistant)
  - Backward compatibility: systems that only know Ed25519 can still verify
    the classical component; systems that know both verify the hybrid

This module provides:

  MLDSALevel         — security level selector (44 / 65 / 87)
  HybridSignature    — container for (ed25519_sig, mldsa_sig) pair
  HybridIdentity     — Ed25519 + ML-DSA keypair with hybrid sign/verify
  HybridDelegationToken — DelegationToken with hybrid root signature
  upgrade_identity   — migrate an existing AgentIdentity to HybridIdentity

ML-DSA parameter sets:
  ML-DSA-44 (Dilithium2) — NIST security level 2 (~AES-128)
  ML-DSA-65 (Dilithium3) — NIST security level 3 (~AES-192)  ← default
  ML-DSA-87 (Dilithium5) — NIST security level 5 (~AES-256)

References:
  - NIST FIPS 204: Module-Lattice-Based Digital Signature Standard (2024)
  - NSA CNSA 2.0 Algorithm Suite (2022)
  - IETF draft-ietf-pquip-hybrid-signature-spectrums
  - dilithium-py: pure-Python ML-DSA implementation (pip install dilithium-py)
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Classical Ed25519 (from sc_identity)
# ---------------------------------------------------------------------------
from sc_identity import AgentIdentity, DelegationToken, Caveat

# ---------------------------------------------------------------------------
# ML-DSA via dilithium-py
# ---------------------------------------------------------------------------
try:
    from dilithium_py.dilithium import Dilithium2, Dilithium3, Dilithium5
    _MLDSA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MLDSA_AVAILABLE = False
    Dilithium2 = Dilithium3 = Dilithium5 = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# MLDSALevel
# ---------------------------------------------------------------------------

class MLDSALevel(str, Enum):
    """
    ML-DSA security level selector.

    Level 2 (ML-DSA-44): ~AES-128 — fast, suitable for high-frequency HID ops
    Level 3 (ML-DSA-65): ~AES-192 — recommended default for IL4/IL5
    Level 5 (ML-DSA-87): ~AES-256 — required for IL6/IL7 / long-lived identities
    """
    LEVEL_2 = "ML-DSA-44"
    LEVEL_3 = "ML-DSA-65"
    LEVEL_5 = "ML-DSA-87"

    def _impl(self):
        """Return the dilithium-py implementation for this level."""
        if not _MLDSA_AVAILABLE:
            raise RuntimeError("pip install dilithium-py  to use ML-DSA")
        return {
            MLDSALevel.LEVEL_2: Dilithium2,
            MLDSALevel.LEVEL_3: Dilithium3,
            MLDSALevel.LEVEL_5: Dilithium5,
        }[self]


# ---------------------------------------------------------------------------
# HybridSignature
# ---------------------------------------------------------------------------

@dataclass
class HybridSignature:
    """
    Container for a hybrid Ed25519 + ML-DSA signature pair.

    Both signatures cover the same message. A verifier that knows both
    algorithms must verify both. A verifier that only knows Ed25519 can
    still verify the classical component (graceful degradation).
    """
    ed25519_sig: bytes          # 64-byte Ed25519 signature
    mldsa_sig: bytes            # ML-DSA signature (2420/3293/4595 bytes for L2/L3/L5)
    mldsa_level: MLDSALevel     # which ML-DSA parameter set was used
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "ed25519_sig": base64.b64encode(self.ed25519_sig).decode(),
            "mldsa_sig": base64.b64encode(self.mldsa_sig).decode(),
            "mldsa_level": self.mldsa_level.value,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HybridSignature":
        return cls(
            ed25519_sig=base64.b64decode(d["ed25519_sig"]),
            mldsa_sig=base64.b64decode(d["mldsa_sig"]),
            mldsa_level=MLDSALevel(d["mldsa_level"]),
            ts=d.get("ts", 0.0),
        )

    def __repr__(self) -> str:
        return (
            f"HybridSignature(ed25519={len(self.ed25519_sig)}B "
            f"mldsa={len(self.mldsa_sig)}B level={self.mldsa_level.value})"
        )


# ---------------------------------------------------------------------------
# HybridIdentity
# ---------------------------------------------------------------------------

class HybridIdentity:
    """
    Ed25519 + ML-DSA hybrid keypair.

    Provides the same interface as AgentIdentity but with hybrid signing.
    Every sign() call produces a HybridSignature; every verify() call
    requires both signatures to be valid.

    Usage::

        identity = HybridIdentity.generate(
            label="Agent-A",
            mldsa_level=MLDSALevel.LEVEL_3,
        )
        sig = identity.sign(b"hello")
        ok  = identity.verify(b"hello", sig)

        # Export / import
        bundle = identity.export_bundle()
        same   = HybridIdentity.from_bundle(bundle)

    The DID is inherited from the Ed25519 component (backward compatible).
    The ML-DSA public key is included in the agent card as an extension field.
    """

    def __init__(
        self,
        classical: AgentIdentity,
        mldsa_pk: bytes,
        mldsa_sk: bytes,
        mldsa_level: MLDSALevel = MLDSALevel.LEVEL_3,
    ) -> None:
        if not _MLDSA_AVAILABLE:
            raise RuntimeError("pip install dilithium-py  to use HybridIdentity")
        self._classical = classical
        self._mldsa_pk = mldsa_pk
        self._mldsa_sk = mldsa_sk
        self._mldsa_level = mldsa_level
        self._impl = mldsa_level._impl()

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def generate(
        cls,
        label: str = "",
        mldsa_level: MLDSALevel = MLDSALevel.LEVEL_3,
    ) -> "HybridIdentity":
        """Generate a fresh Ed25519 + ML-DSA hybrid keypair."""
        classical = AgentIdentity.generate(label=label)
        impl = mldsa_level._impl()
        pk, sk = impl.keygen()
        return cls(classical, pk, sk, mldsa_level)

    @classmethod
    def from_bundle(cls, bundle: dict) -> "HybridIdentity":
        """Deserialise from an export_bundle() dict."""
        classical = AgentIdentity.from_private_pem(
            bundle["ed25519_private_pem"].encode(),
            label=bundle.get("label", ""),
        )
        level = MLDSALevel(bundle["mldsa_level"])
        return cls(
            classical=classical,
            mldsa_pk=base64.b64decode(bundle["mldsa_pk"]),
            mldsa_sk=base64.b64decode(bundle["mldsa_sk"]),
            mldsa_level=level,
        )

    # ── properties (delegate to classical) ───────────────────────────────

    @property
    def label(self) -> str:
        return self._classical.label

    @property
    def did(self) -> str:
        """DID is inherited from the Ed25519 component (backward compatible)."""
        return self._classical.did

    @property
    def public_key_hex(self) -> str:
        return self._classical.public_key_hex

    @property
    def mldsa_public_key_b64(self) -> str:
        return base64.b64encode(self._mldsa_pk).decode()

    @property
    def mldsa_level(self) -> MLDSALevel:
        return self._mldsa_level

    @property
    def classical_identity(self) -> AgentIdentity:
        return self._classical

    # ── sign / verify ─────────────────────────────────────────────────────

    def sign(self, data: bytes) -> HybridSignature:
        """
        Sign *data* with both Ed25519 and ML-DSA.
        Returns a HybridSignature containing both signatures.
        """
        ed_sig = self._classical.sign(data)
        ml_sig = self._impl.sign(self._mldsa_sk, data)
        return HybridSignature(
            ed25519_sig=ed_sig,
            mldsa_sig=ml_sig,
            mldsa_level=self._mldsa_level,
        )

    def verify(self, data: bytes, sig: HybridSignature) -> bool:
        """
        Verify a HybridSignature. BOTH signatures must be valid.
        Returns False if either signature is invalid.
        """
        if not self._classical.verify(data, sig.ed25519_sig):
            return False
        try:
            return bool(self._impl.verify(self._mldsa_pk, data, sig.mldsa_sig))
        except Exception:
            return False

    @classmethod
    def verify_with_pubkeys(
        cls,
        ed25519_pubkey_hex: str,
        mldsa_pk: bytes,
        mldsa_level: MLDSALevel,
        data: bytes,
        sig: HybridSignature,
    ) -> bool:
        """
        Verify a HybridSignature given only the public keys (no private key).
        Both signatures must be valid.
        """
        if not AgentIdentity.verify_with_pubkey_hex(ed25519_pubkey_hex, data, sig.ed25519_sig):
            return False
        try:
            impl = mldsa_level._impl()
            return bool(impl.verify(mldsa_pk, data, sig.mldsa_sig))
        except Exception:
            return False

    # ── agent card ────────────────────────────────────────────────────────

    def to_agent_card(self) -> dict:
        """
        A2A-compatible agent card with hybrid cryptographic attestation.
        Extends the classical agent card with ML-DSA public key fields.
        """
        card = self._classical.to_agent_card()
        card.update({
            "schema": "selfconnect-agent-card-v2-hybrid",
            "mldsa_level": self._mldsa_level.value,
            "mldsa_public_key_b64": self.mldsa_public_key_b64,
        })
        return card

    def sign_agent_card(self, card: Optional[dict] = None) -> dict:
        """
        Return the agent card with a hybrid signature.
        The ``hybrid_signature`` field covers all other fields.
        """
        if card is None:
            card = self.to_agent_card()
        canonical = json.dumps(card, sort_keys=True, separators=(",", ":")).encode()
        hybrid_sig = self.sign(canonical)
        return {**card, "hybrid_signature": hybrid_sig.to_dict()}

    # ── serialisation ─────────────────────────────────────────────────────

    def export_bundle(self) -> dict:
        """
        Export the full keypair bundle (SENSITIVE — contains private keys).
        Store encrypted at rest.
        """
        return {
            "schema": "selfconnect-hybrid-identity-bundle-v1",
            "label": self.label,
            "did": self.did,
            "ed25519_private_pem": self._classical.private_pem().decode(),
            "mldsa_level": self._mldsa_level.value,
            "mldsa_pk": base64.b64encode(self._mldsa_pk).decode(),
            "mldsa_sk": base64.b64encode(self._mldsa_sk).decode(),
        }

    def export_public_bundle(self) -> dict:
        """Export only the public keys (safe to share)."""
        return {
            "schema": "selfconnect-hybrid-pubkey-bundle-v1",
            "label": self.label,
            "did": self.did,
            "ed25519_pubkey_hex": self.public_key_hex,
            "mldsa_level": self._mldsa_level.value,
            "mldsa_public_key_b64": self.mldsa_public_key_b64,
        }

    def __repr__(self) -> str:
        return (
            f"HybridIdentity(label={self.label!r} "
            f"did={self.did[:30]}... "
            f"mldsa={self._mldsa_level.value})"
        )


# ---------------------------------------------------------------------------
# HybridDelegationToken
# ---------------------------------------------------------------------------

class HybridDelegationToken(DelegationToken):
    """
    DelegationToken with a hybrid Ed25519 + ML-DSA root signature.

    Extends DelegationToken by replacing the Ed25519-only root_signature
    with a HybridSignature. The HMAC chain (caveats) is unchanged.

    Usage::

        issuer = HybridIdentity.generate(label="Agent-A")
        token  = HybridDelegationToken.mint_hybrid(
            issuer=issuer,
            subject_did="did:key:z...",
            scope=["tool:bash"],
            expires_in=3600,
        )
        ok, reason = token.verify_hybrid(issuer_pubkey_hex=issuer.public_key_hex,
                                          mldsa_pk=issuer._mldsa_pk,
                                          mldsa_level=issuer.mldsa_level)
    """

    SCHEMA = "selfconnect-hybrid-delegation-v1"

    def __init__(
        self,
        *args,
        hybrid_root_sig: Optional[HybridSignature] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.hybrid_root_sig = hybrid_root_sig

    @classmethod
    def mint_hybrid(
        cls,
        issuer: HybridIdentity,
        subject_did: str,
        scope: list[str],
        expires_in: float = 3600.0,
    ) -> "HybridDelegationToken":
        """Mint a new HybridDelegationToken signed by a HybridIdentity."""
        # Mint the classical token first (inherits all classical logic)
        classical = DelegationToken.mint(
            issuer=issuer.classical_identity,
            subject_did=subject_did,
            scope=scope,
            expires_in=expires_in,
        )
        # Re-sign root fields with hybrid signature
        root_fields = {
            "schema": cls.SCHEMA,
            "token_id": classical.token_id,
            "issuer_did": classical.issuer_did,
            "issuer_pubkey_hex": classical.issuer_pubkey_hex,
            "subject_did": classical.subject_did,
            "scope": sorted(classical.scope),
            "issued_at": classical.issued_at,
            "expires_at": classical.expires_at,
        }
        canonical = json.dumps(root_fields, sort_keys=True, separators=(",", ":")).encode()
        hybrid_sig = issuer.sign(canonical)

        return cls(
            token_id=classical.token_id,
            issuer_did=classical.issuer_did,
            issuer_pubkey_hex=classical.issuer_pubkey_hex,
            subject_did=classical.subject_did,
            scope=classical.scope,
            issued_at=classical.issued_at,
            expires_at=classical.expires_at,
            caveats=[],
            root_signature=hybrid_sig.ed25519_sig,
            chain_mac=classical.chain_mac,
            hybrid_root_sig=hybrid_sig,
        )

    def verify_hybrid(
        self,
        issuer_pubkey_hex: str,
        mldsa_pk: bytes,
        mldsa_level: MLDSALevel,
        now: Optional[float] = None,
    ) -> tuple[bool, str]:
        """
        Verify the hybrid root signature (both Ed25519 and ML-DSA).
        Falls back to classical verify if hybrid_root_sig is not present.
        """
        # Classical checks first
        ok, reason = self.verify(issuer_pubkey_hex=issuer_pubkey_hex, now=now)
        if not ok:
            return False, reason

        if self.hybrid_root_sig is None:
            return False, "no hybrid signature present"

        root_fields = {
            "schema": self.SCHEMA,
            "token_id": self.token_id,
            "issuer_did": self.issuer_did,
            "issuer_pubkey_hex": self.issuer_pubkey_hex,
            "subject_did": self.subject_did,
            "scope": sorted(self.scope),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }
        canonical = json.dumps(root_fields, sort_keys=True, separators=(",", ":")).encode()

        if not HybridIdentity.verify_with_pubkeys(
            issuer_pubkey_hex, mldsa_pk, mldsa_level, canonical, self.hybrid_root_sig
        ):
            return False, "hybrid root signature invalid (ML-DSA component failed)"

        return True, ""

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["schema"] = self.SCHEMA
        if self.hybrid_root_sig:
            d["hybrid_root_sig"] = self.hybrid_root_sig.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "HybridDelegationToken":
        if d.get("schema") != cls.SCHEMA:
            raise ValueError(f"Not a hybrid delegation token (schema={d.get('schema')!r})")
        hybrid_sig = None
        if "hybrid_root_sig" in d:
            hybrid_sig = HybridSignature.from_dict(d["hybrid_root_sig"])
        return cls(
            token_id=d["token_id"],
            issuer_did=d["issuer_did"],
            issuer_pubkey_hex=d["issuer_pubkey_hex"],
            subject_did=d["subject_did"],
            scope=d["scope"],
            issued_at=d["issued_at"],
            expires_at=d["expires_at"],
            caveats=[Caveat(c["key"], c["value"]) for c in d.get("caveats", [])],
            root_signature=base64.b64decode(d["root_signature"]),
            chain_mac=base64.b64decode(d["chain_mac"]),
            hybrid_root_sig=hybrid_sig,
        )


# ---------------------------------------------------------------------------
# upgrade_identity  — migrate AgentIdentity → HybridIdentity
# ---------------------------------------------------------------------------

def upgrade_identity(
    classical: AgentIdentity,
    mldsa_level: MLDSALevel = MLDSALevel.LEVEL_3,
) -> HybridIdentity:
    """
    Migrate an existing AgentIdentity to a HybridIdentity.

    The Ed25519 keypair is preserved unchanged — the DID, existing
    signatures, and delegation tokens remain valid. A new ML-DSA keypair
    is generated and paired with the existing classical identity.

    Usage::

        old_identity = AgentIdentity.from_private_pem(pem, label="Agent-A")
        new_identity = upgrade_identity(old_identity, MLDSALevel.LEVEL_3)
        # old_identity.did == new_identity.did  (backward compatible)
    """
    if not _MLDSA_AVAILABLE:
        raise RuntimeError("pip install dilithium-py  to use upgrade_identity")
    impl = mldsa_level._impl()
    pk, sk = impl.keygen()
    return HybridIdentity(
        classical=classical,
        mldsa_pk=pk,
        mldsa_sk=sk,
        mldsa_level=mldsa_level,
    )
