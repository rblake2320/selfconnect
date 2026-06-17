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
    birth_id: str = ""

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
        birth_id: str = "",
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
            birth_id=birth_id,
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
        birth_id: str | None = None,
        ttl_s: float = 30.0,
        now: float | None = None,
    ) -> LeaseValidation:
        validation = self.validate_ui_fallback(
            mesh=mesh,
            role=role,
            generation=generation,
            hwnd=hwnd,
            owner_sid=owner_sid,
            birth_id=birth_id,
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
        birth_id: str | None = None,
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
        if birth_id is not None and birth_id != lease.birth_id:
            return LeaseValidation(LeaseDecision.DENY, "birth_id mismatch", lease)
        return LeaseValidation(LeaseDecision.ALLOW, "role generation and hwnd match", lease)


GOVERNED_PROFILE = "governed"

# Sentinel returned when the runtime OS SID is unavailable. It never matches a
# real issued lease (which hashes a real SID), so governed mode fails closed.
UNKNOWN_SID = "<unknown-sid>"


def current_owner_sid(injected: str | None = None) -> str:
    """Resolve the current OS owner SID for governed lease checks.

    Returns ``injected`` verbatim when provided. Otherwise this is a best-effort
    placeholder: real runtime OS SID integration
    (``OpenProcessToken`` -> ``GetTokenInformation(TokenUser)`` ->
    ``ConvertSidToStringSid``) is the documented NEXT STEP. Until that lands,
    callers should inject ``owner_sid`` explicitly; if nothing is injected this
    returns the sentinel ``"<unknown-sid>"`` so governed mode FAILS CLOSED
    instead of silently allowing an unauthenticated action.
    """
    if injected is not None:
        return injected
    return UNKNOWN_SID


def evaluate_lease_gate(
    *,
    profile: str = "explore",
    table: RoleLeaseTable | None = None,
    mesh: str = "default",
    role: str | None = None,
    generation: int | None = None,
    hwnd: int = 0,
    owner_sid: str | None = None,
    birth_id: str | None = None,
    now: float | None = None,
) -> LeaseValidation:
    """Optional governed enforcement layered over the explore-mode target guard.

    Explore mode (default) is a no-op ALLOW: lease is not required. Governed mode
    is triggered either by ``profile == "governed"`` or by the presence of
    explicit lease fields (``role`` or ``generation``). In governed mode the
    caller must present a lease table plus ``role`` + ``generation``; the
    decision is delegated to ``RoleLeaseTable.validate_ui_fallback`` so only the
    ``owner_sid_hash`` ever flows into the result, never a raw SID.
    """
    lease_fields_present = (role is not None) or (generation is not None)
    governed = (str(profile).strip().lower() == GOVERNED_PROFILE) or lease_fields_present

    if not governed:
        return LeaseValidation(LeaseDecision.ALLOW, "explore mode: lease not required")

    if table is None:
        return LeaseValidation(LeaseDecision.DENY, "governed mode requires a lease table")
    if not role or generation is None:
        return LeaseValidation(LeaseDecision.DENY, "governed mode requires role and generation")

    sid = current_owner_sid(owner_sid)
    return table.validate_ui_fallback(
        mesh=mesh,
        role=role,
        generation=int(generation),
        hwnd=int(hwnd),
        owner_sid=sid,
        birth_id=birth_id,
        now=now,
    )
