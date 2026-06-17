"""Role lease and generation gates for SelfConnect mesh control planes.

This module is intentionally transport-neutral. A named-pipe control plane,
file registry, MCP adapter, or service daemon can issue leases through the same
logic. UI fallback is allowed only when the caller presents the current
role + generation + hwnd tuple owned by the same OS identity.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum


class LeaseDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class RoleLease:
    mesh: str
    role: str
    generation: int
    lease_id: str
    hwnd: int
    pid: int
    exe_name: str
    class_name: str
    title_hash: str
    owner_sid_hash: str
    issued_at: float
    expires_at: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class LeaseValidation:
    decision: LeaseDecision
    reason: str
    lease: RoleLease | None = None

    @property
    def ok(self) -> bool:
        return self.decision == LeaseDecision.ALLOW

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "ok": self.ok,
            "lease": self.lease.to_dict() if self.lease else None,
        }


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_sid(owner_sid: str) -> str:
    return hash_text(owner_sid or "<unknown-sid>")


class RoleLeaseTable:
    """In-memory monotonic role lease table.

    Generation numbers are per mesh+role. A migrated role gets a higher
    generation, and stale actions carrying an old generation fail closed.
    """

    def __init__(self) -> None:
        self._leases: dict[tuple[str, str], RoleLease] = {}

    def issue(
        self,
        *,
        mesh: str,
        role: str,
        hwnd: int,
        pid: int,
        exe_name: str,
        class_name: str,
        title: str,
        owner_sid: str,
        ttl_s: float = 30.0,
        now: float | None = None,
    ) -> RoleLease:
        if not role.strip():
            raise ValueError("role is required")
        if not mesh.strip():
            raise ValueError("mesh is required")
        if int(hwnd) <= 0:
            raise ValueError("hwnd must be positive")
        if int(pid) < 0:
            raise ValueError("pid must be non-negative")
        now = time.time() if now is None else float(now)
        key = (mesh, role)
        generation = self._leases[key].generation + 1 if key in self._leases else 1
        lease = RoleLease(
            mesh=mesh,
            role=role,
            generation=generation,
            lease_id=uuid.uuid4().hex,
            hwnd=int(hwnd),
            pid=int(pid),
            exe_name=exe_name,
            class_name=class_name,
            title_hash=hash_text(title),
            owner_sid_hash=hash_sid(owner_sid),
            issued_at=now,
            expires_at=now + float(ttl_s),
        )
        self._leases[key] = lease
        return lease

    def current(self, mesh: str, role: str) -> RoleLease | None:
        return self._leases.get((mesh, role))

    def renew(
        self,
        *,
        mesh: str,
        role: str,
        generation: int,
        hwnd: int,
        owner_sid: str,
        ttl_s: float = 30.0,
        now: float | None = None,
    ) -> LeaseValidation:
        validation = self.validate_ui_fallback(
            mesh=mesh,
            role=role,
            generation=generation,
            hwnd=hwnd,
            owner_sid=owner_sid,
            now=now,
        )
        if not validation.ok or validation.lease is None:
            return validation
        now = time.time() if now is None else float(now)
        validation.lease.expires_at = now + float(ttl_s)
        return LeaseValidation(LeaseDecision.ALLOW, "renewed", validation.lease)

    def validate_ui_fallback(
        self,
        *,
        mesh: str,
        role: str,
        generation: int,
        hwnd: int,
        owner_sid: str,
        now: float | None = None,
    ) -> LeaseValidation:
        lease = self.current(mesh, role)
        if lease is None:
            return LeaseValidation(LeaseDecision.DENY, "role has no active lease")
        now = time.time() if now is None else float(now)
        if now > lease.expires_at:
            return LeaseValidation(LeaseDecision.DENY, "lease expired", lease)
        if int(generation) != lease.generation:
            return LeaseValidation(LeaseDecision.DENY, "generation mismatch", lease)
        if int(hwnd) != lease.hwnd:
            return LeaseValidation(LeaseDecision.DENY, "hwnd mismatch", lease)
        if hash_sid(owner_sid) != lease.owner_sid_hash:
            return LeaseValidation(LeaseDecision.DENY, "owner sid mismatch", lease)
        return LeaseValidation(LeaseDecision.ALLOW, "role generation and hwnd match", lease)
