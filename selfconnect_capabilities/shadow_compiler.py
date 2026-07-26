"""Compile authenticated execution traces into non-executable shadow skills."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from sc_identity import AgentIdentity

from .broker import CapabilityBroker
from .evidence import EvidenceStore
from .integrity import IntegrityKey, canonical_bytes
from .models import INSTRUCTION_LIKE, SkillManifest, validate_inputs
from .permissions import Authority
from .registry import SkillRegistry

MIN_SOURCE_RUNS = 3
MIN_REPLAY_RUNS = 3


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise ValueError(f"unsupported trace argument type: {type(value).__name__}")


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


class ShadowSkillCompiler:
    """Trusted-host compiler; candidates never enter the runtime registry."""

    def __init__(
        self,
        root: Path,
        registry: SkillRegistry,
        *,
        deterministic_verifiers: frozenset[str],
        trusted_approvers: dict[str, str] | None = None,
    ):
        self.root = root.resolve()
        self.registry = registry
        self.deterministic_verifiers = deterministic_verifiers
        self.trusted_approvers = dict(trusted_approvers or {})
        self.shadow_dir = self.root / "shadow"
        self.replay_dir = self.root / "replays"
        self.review_dir = self.root / "reviews"
        for path in (self.shadow_dir, self.replay_dir, self.review_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.integrity = IntegrityKey(self.root)

    def compile_from_evidence(
        self,
        evidence: EvidenceStore,
        runs: list[list[str]],
        *,
        name: str,
        version: str,
        description: str,
        compiler_principal: str,
    ) -> dict[str, Any]:
        """Distill equal-shaped successful runs selected by authenticated event ids."""
        if len(runs) < MIN_SOURCE_RUNS:
            raise ValueError(f"at least {MIN_SOURCE_RUNS} source runs are required")
        if not name.startswith("shadow."):
            raise ValueError("shadow candidate names must start with 'shadow.'")
        if INSTRUCTION_LIKE.search(description):
            raise ValueError("candidate description contains instruction-like text")
        records_by_id = {record["event_id"]: record for record in evidence.records()}
        traces: list[list[dict[str, Any]]] = []
        for run in runs:
            if not run:
                raise ValueError("source runs may not be empty")
            trace = []
            for event_id in run:
                record = records_by_id.get(event_id)
                if record is None:
                    raise ValueError(f"unknown evidence event: {event_id}")
                if record.get("event") != "capability_completed":
                    raise ValueError("only completed capability evidence may be compiled")
                details = record.get("details", {})
                if details.get("ok") is not True:
                    raise ValueError("failed capability evidence may not be compiled")
                trace.append(record)
            traces.append(trace)
        shape = [record["details"]["capability"] for record in traces[0]]
        if any([record["details"]["capability"] for record in trace] != shape for trace in traces):
            raise ValueError("source runs do not share one capability sequence")

        inputs: dict[str, dict[str, Any]] = {}
        steps = []
        permissions: set[str] = set()
        source_ids = []
        for index, capability in enumerate(shape):
            manifest = self.registry.get(capability)
            digest = manifest.manifest_digest or manifest.digest()
            if any(trace[index]["details"].get("manifest_digest") != digest for trace in traces):
                raise ValueError("source evidence manifest digest no longer matches registry")
            if any(name not in self.deterministic_verifiers for name in manifest.verification):
                raise ValueError("source capability uses a non-approved verifier")
            arguments = [trace[index]["details"].get("arguments", {}) for trace in traces]
            if not all(isinstance(value, dict) for value in arguments):
                raise ValueError("source arguments must be objects")
            template: dict[str, Any] = {}
            for argument in sorted(arguments[0]):
                values = [value.get(argument) for value in arguments]
                if any(argument not in value for value in arguments):
                    raise ValueError("source argument shape changed across runs")
                # Paths and changing values are inputs. Stable, non-path constants
                # remain fixed preconditions in the shadow procedure.
                parameterize = "[redacted]" in values
                parameterize = parameterize or len({_sha256(value) for value in values}) > 1
                parameterize = parameterize or (
                    isinstance(values[0], str) and Path(values[0]).is_absolute()
                )
                if parameterize:
                    input_name = f"step_{index + 1}_{argument}"
                    value_type = _json_type(values[0])
                    if any(_json_type(value) != value_type for value in values):
                        raise ValueError("source input type changed across runs")
                    inputs[input_name] = {"type": value_type}
                    template[argument] = {"$input": input_name}
                else:
                    template[argument] = values[0]
            permissions.update(manifest.permissions)
            source_ids.append([trace[index]["event_id"] for trace in traces])
            steps.append({
                "index": index + 1,
                "capability": capability,
                "manifest_digest": digest,
                "arguments": template,
                "permissions": list(manifest.permissions),
                "verifiers": list(manifest.verification),
                "output_shape": self._output_shape(
                    [trace[index]["details"].get("output", {}) for trace in traces]
                ),
            })

        candidate = {
            "schema": "selfconnect.shadow-skill-candidate.v1",
            "candidate_id": uuid.uuid4().hex,
            "status": "shadow",
            "name": name,
            "version": version,
            "description": description,
            "created_at": time.time(),
            "compiler_principal": compiler_principal,
            "source_run_count": len(runs),
            "source_event_ids_by_step": source_ids,
            "input_schema": {
                "type": "object",
                "properties": inputs,
                "required": sorted(inputs),
                "additionalProperties": False,
            },
            "permissions": sorted(permissions),
            "steps": steps,
            "constraints": {
                "generated_code": False,
                "runtime_registered": False,
                "model_self_promotion": False,
                "minimum_replays": MIN_REPLAY_RUNS,
                "separate_approval_required": True,
            },
        }
        candidate["candidate_digest"] = _sha256(candidate)
        candidate["candidate_hmac"] = self.integrity.digest(candidate)
        self._write_json(self.candidate_path(candidate["candidate_id"]), candidate)
        return candidate

    def candidate_path(self, candidate_id: str) -> Path:
        return self.shadow_dir / f"{candidate_id}.json"

    def load_candidate(self, candidate_id: str) -> dict[str, Any]:
        value = json.loads(self.candidate_path(candidate_id).read_text(encoding="utf-8"))
        self._verify_candidate(value)
        return value

    def replay(
        self,
        candidate_id: str,
        bindings: dict[str, Any],
        *,
        broker: CapabilityBroker,
        authority: Authority,
    ) -> dict[str, Any]:
        candidate = self.load_candidate(candidate_id)
        self._validate_bindings(candidate, bindings)
        results = []
        for step in candidate["steps"]:
            arguments = self._resolve(step["arguments"], bindings)
            result = broker.execute(
                step["capability"],
                arguments,
                authority,
                expected_manifest_digest=step["manifest_digest"],
                evidence_context={
                    "shadow_candidate_id": candidate_id,
                    "shadow_replay": True,
                },
            ).as_dict()
            shape_verification = self._matches_output_shape(
                result.get("output", {}),
                step["output_shape"],
            )
            results.append({
                "step": step["index"],
                "arguments": arguments,
                "result": result,
                "shape_verification": shape_verification,
            })
            if not result["ok"] or not shape_verification["ok"]:
                break
        report = {
            "schema": "selfconnect.shadow-replay.v1",
            "replay_id": uuid.uuid4().hex,
            "candidate_id": candidate_id,
            "candidate_digest": candidate["candidate_digest"],
            "created_at": time.time(),
            "ok": len(results) == len(candidate["steps"]) and all(
                item["result"]["ok"] and item["shape_verification"]["ok"]
                for item in results
            ),
            "results": results,
        }
        report["report_hmac"] = self.integrity.digest(report)
        self._write_json(self.replay_dir / f"{report['replay_id']}.json", report)
        return report

    def adversarial_validate(self, candidate_id: str) -> dict[str, Any]:
        candidate = self.load_candidate(candidate_id)
        manifest_permissions = sorted({
            permission
            for step in candidate["steps"]
            for permission in self.registry.get(step["capability"]).permissions
        })
        cases = {
            "description_injection_rejected": not any(
                INSTRUCTION_LIKE.search(text) for text in _walk_strings(candidate["description"])
            ),
            "argument_smuggling_rejected": (
                candidate["input_schema"].get("additionalProperties") is False
            ),
            "privilege_escalation_rejected": candidate["permissions"] == manifest_permissions,
            "manifest_rebinding_enforced": all(
                step["manifest_digest"]
                == (
                    self.registry.get(step["capability"]).manifest_digest
                    or self.registry.get(step["capability"]).digest()
                )
                for step in candidate["steps"]
            ),
            "arbitrary_verifier_code_absent": candidate["constraints"]["generated_code"] is False
            and all(
                verifier in self.deterministic_verifiers
                for step in candidate["steps"]
                for verifier in step["verifiers"]
            ),
            "deterministic_output_shapes_present": all(
                bool(step["output_shape"]) for step in candidate["steps"]
            ),
            "runtime_registration_absent": candidate["constraints"]["runtime_registered"] is False,
        }
        report = {
            "schema": "selfconnect.shadow-adversarial.v1",
            "candidate_id": candidate_id,
            "candidate_digest": candidate["candidate_digest"],
            "created_at": time.time(),
            "ok": all(cases.values()),
            "cases": cases,
        }
        report["report_hmac"] = self.integrity.digest(report)
        self._write_json(self.replay_dir / f"{candidate_id}.adversarial.json", report)
        return report

    def review_eligibility(self, candidate_id: str) -> dict[str, Any]:
        candidate = self.load_candidate(candidate_id)
        replay_reports = []
        for path in self.replay_dir.glob("*.json"):
            report = json.loads(path.read_text(encoding="utf-8"))
            if (
                report.get("schema") == "selfconnect.shadow-replay.v1"
                and report.get("candidate_id") == candidate_id
            ):
                replay_reports.append(report)
        valid_replays = [
            report for report in replay_reports
            if self._verify_report(report) and report.get("ok") is True
        ]
        adversarial_path = self.replay_dir / f"{candidate_id}.adversarial.json"
        adversarial_ok = False
        if adversarial_path.exists():
            adversarial = json.loads(adversarial_path.read_text(encoding="utf-8"))
            adversarial_ok = self._verify_report(adversarial) and adversarial.get("ok") is True
        eligible = len(valid_replays) >= MIN_REPLAY_RUNS and adversarial_ok
        return {
            "ok": eligible,
            "candidate_id": candidate_id,
            "candidate_digest": candidate["candidate_digest"],
            "successful_authenticated_replays": len(valid_replays),
            "minimum_replays": MIN_REPLAY_RUNS,
            "adversarial_ok": adversarial_ok,
            "still_shadow": True,
            "separate_approval_required": True,
        }

    def approve(
        self,
        candidate_id: str,
        *,
        signed_review: dict[str, Any],
    ) -> dict[str, Any]:
        candidate = self.load_candidate(candidate_id)
        eligibility = self.review_eligibility(candidate_id)
        if not eligibility["ok"]:
            raise PermissionError("candidate has not passed replay and adversarial gates")
        allowed = {
            "candidate_id", "candidate_digest", "approver_did",
            "review_statement", "created_at", "publishes_runtime_adapter",
            "human_reviewed", "signature_b64",
        }
        if set(signed_review) != allowed:
            raise ValueError("signed review shape is invalid")
        signature_b64 = str(signed_review["signature_b64"])
        payload = {key: value for key, value in signed_review.items() if key != "signature_b64"}
        approver = str(payload["approver_did"])
        if approver == candidate["compiler_principal"]:
            raise PermissionError("a separate named approver is required")
        if payload["candidate_id"] != candidate_id:
            raise ValueError("approval candidate id mismatch")
        if payload["candidate_digest"] != candidate["candidate_digest"]:
            raise ValueError("approval candidate digest mismatch")
        if payload["publishes_runtime_adapter"] is not False:
            raise PermissionError("shadow approval cannot publish a runtime adapter")
        if payload["human_reviewed"] is not True:
            raise PermissionError("a human review assertion is required")
        if len(str(payload["review_statement"]).strip()) < 20:
            raise ValueError("approval requires a substantive review statement")
        pubkey = self.trusted_approvers.get(approver)
        if not pubkey:
            raise PermissionError("approver identity is not trusted")
        try:
            signature = base64.b64decode(signature_b64, validate=True)
        except Exception as exc:
            raise ValueError("approval signature encoding is invalid") from exc
        if not AgentIdentity.verify_with_pubkey_hex(
            pubkey,
            canonical_bytes(payload),
            signature,
        ):
            raise PermissionError("approval signature verification failed")
        approval = {"schema": "selfconnect.shadow-approval.v1", **payload, "signature_b64": signature_b64}
        approval["approval_hmac"] = self.integrity.digest(approval)
        self._write_json(self.review_dir / f"{candidate_id}.approval.json", approval)
        return approval

    def _verify_candidate(self, value: dict[str, Any]) -> None:
        actual_hmac = str(value.pop("candidate_hmac", ""))
        try:
            if actual_hmac != self.integrity.digest(value):
                raise ValueError("shadow candidate authentication failed")
            actual_digest = str(value.pop("candidate_digest", ""))
            if actual_digest != _sha256(value):
                raise ValueError("shadow candidate digest failed")
            if value.get("status") != "shadow":
                raise ValueError("compiler accepts shadow candidates only")
        finally:
            value["candidate_digest"] = locals().get("actual_digest", "")
            value["candidate_hmac"] = actual_hmac

    def _verify_report(self, value: dict[str, Any]) -> bool:
        actual = str(value.pop("report_hmac", ""))
        try:
            return actual == self.integrity.digest(value)
        finally:
            value["report_hmac"] = actual

    @staticmethod
    def _output_shape(outputs: list[Any]) -> dict[str, str]:
        if not all(isinstance(output, dict) for output in outputs):
            raise ValueError("source outputs must be objects")
        shared = set(outputs[0])
        for output in outputs[1:]:
            shared.intersection_update(output)
        return {name: _json_type(outputs[0][name]) for name in sorted(shared)}

    @staticmethod
    def _matches_output_shape(output: Any, expected: dict[str, str]) -> dict[str, Any]:
        if not isinstance(output, dict):
            return {"ok": False, "reason": "output is not an object"}
        missing = sorted(set(expected) - set(output))
        wrong_types = sorted(
            name
            for name, expected_type in expected.items()
            if name in output and _json_type(output[name]) != expected_type
        )
        return {
            "ok": not missing and not wrong_types,
            "missing": missing,
            "wrong_types": wrong_types,
        }

    @staticmethod
    def _resolve(value: Any, bindings: dict[str, Any]) -> Any:
        if isinstance(value, dict) and set(value) == {"$input"}:
            return bindings[value["$input"]]
        if isinstance(value, dict):
            return {key: ShadowSkillCompiler._resolve(item, bindings) for key, item in value.items()}
        if isinstance(value, list):
            return [ShadowSkillCompiler._resolve(item, bindings) for item in value]
        return value

    @staticmethod
    def _validate_bindings(candidate: dict[str, Any], bindings: dict[str, Any]) -> None:
        probe = SkillManifest(
            name="shadow.binding-check",
            version="1",
            description="Validate shadow replay bindings.",
            adapter="shadow-binding-check",
            input_schema=candidate["input_schema"],
        )
        validate_inputs(probe, bindings)

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temp = path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, path)
