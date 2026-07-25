"""Permission-gated dispatch to trusted server-side capability adapters."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .evidence import EvidenceStore
from .models import SkillManifest, validate_inputs
from .permissions import Authority, PermissionDenied
from .registry import SkillRegistry

Adapter = Callable[..., dict[str, Any]]
Verifier = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class CapabilityResult:
    ok: bool
    capability: str
    output: dict[str, Any]
    verification: dict[str, Any]
    evidence_id: str
    elapsed_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "capability": self.capability,
            "output": self.output,
            "verification": self.verification,
            "evidence_id": self.evidence_id,
            "elapsed_ms": self.elapsed_ms,
        }


class CapabilityBroker:
    def __init__(self, registry: SkillRegistry, evidence: EvidenceStore):
        self.registry = registry
        self.evidence = evidence
        self._adapters: dict[str, Adapter] = {}
        self._verifiers: dict[str, Verifier] = {}

    def register_adapter(self, name: str, adapter: Adapter) -> None:
        if name in self._adapters:
            raise ValueError(f"adapter already registered: {name}")
        self._adapters[name] = adapter

    def register_verifier(self, name: str, verifier: Verifier) -> None:
        self._verifiers[name] = verifier

    def authorize(
        self,
        capability: str,
        authority: Authority,
        *,
        evidence_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record and return the broker's policy decision before any adapter runs."""
        manifest = self.registry.get(capability)
        missing = authority.missing(manifest.permissions)
        allowed = not missing
        record = self.evidence.append(
            "capability_policy_decision",
            capability=capability,
            principal=authority.principal,
            allowed=allowed,
            missing_permissions=missing,
            manifest_digest=manifest.manifest_digest or manifest.digest(),
            **(evidence_context or {}),
        )
        return {
            "ok": allowed,
            "allowed": allowed,
            "capability": capability,
            "missing_permissions": missing,
            "manifest_digest": manifest.manifest_digest or manifest.digest(),
            "evidence_id": record["event_id"],
        }

    def execute(
        self,
        capability: str,
        arguments: dict[str, Any],
        authority: Authority,
        *,
        expected_manifest_digest: str,
        evidence_context: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        started = time.perf_counter()
        manifest = self.registry.get(capability)
        actual_digest = manifest.manifest_digest or manifest.digest()
        if expected_manifest_digest != actual_digest:
            record = self.evidence.append(
                "capability_manifest_mismatch",
                capability=capability,
                principal=authority.principal,
                expected_manifest_digest=expected_manifest_digest,
                actual_manifest_digest=actual_digest,
                **(evidence_context or {}),
            )
            return CapabilityResult(
                False,
                capability,
                {"ok": False, "error": "capability manifest digest mismatch"},
                {"ok": False, "reason": "manifest_digest_mismatch"},
                record["event_id"],
                round((time.perf_counter() - started) * 1000, 3),
            )
        context = evidence_context or {}
        decision = self.authorize(
            capability,
            authority,
            evidence_context=context,
        )
        if not decision["allowed"]:
            try:
                authority.require(manifest.permissions)
            except PermissionDenied as exc:
                reason = str(exc)
            else:  # pragma: no cover - defensive consistency guard
                reason = "capability policy denied execution"
            record = self.evidence.append(
                "capability_denied",
                capability=capability,
                principal=authority.principal,
                reason=reason,
                policy_evidence_id=decision["evidence_id"],
                manifest_digest=manifest.manifest_digest or manifest.digest(),
                **context,
            )
            return CapabilityResult(
                False, capability, {"ok": False, "error": reason},
                {"ok": False, "reason": "permission_denied"},
                record["event_id"], round((time.perf_counter() - started) * 1000, 3),
            )
        validate_inputs(manifest, arguments)
        adapter = self._adapters.get(manifest.adapter)
        if adapter is None:
            raise RuntimeError(f"trusted adapter is not registered: {manifest.adapter}")
        try:
            output = adapter(**arguments)
            if not isinstance(output, dict):
                output = {"ok": False, "error": "adapter returned a non-object result"}
        except Exception as exc:
            output = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        verification = self._verify(manifest, arguments, output)
        ok = bool(output.get("ok")) and bool(verification.get("ok"))
        record = self.evidence.append(
            "capability_completed",
            capability=capability,
            principal=authority.principal,
            arguments=arguments,
            output=output,
            verification=verification,
            ok=ok,
            manifest_digest=manifest.manifest_digest or manifest.digest(),
            **context,
        )
        return CapabilityResult(
            ok, capability, output, verification, record["event_id"],
            round((time.perf_counter() - started) * 1000, 3),
        )

    def _verify(
        self,
        manifest: SkillManifest,
        arguments: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        checks = []
        for name in manifest.verification:
            verifier = self._verifiers.get(name)
            if verifier is None:
                checks.append({"name": name, "ok": False, "error": "verifier not registered"})
            else:
                checks.append({"name": name, **verifier(arguments, output)})
        if not checks:
            checks.append({"name": "adapter_result", "ok": bool(output.get("ok"))})
        return {"ok": all(bool(item.get("ok")) for item in checks), "checks": checks}
