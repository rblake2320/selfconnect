"""Validate CI/package/release coherence for an explicit candidate root.

This is intentionally candidate-root based: the coordinator can point it at a
merged worktree without changing the current checkout or refreshing claim
hashes.  Missing implementation modules are implementation failures; release
claim hash drift is reported separately as expected pre-merge evidence.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

REQUIRED_MODULES = (
    "sc_assignment_protocol.py",
    "sc_assignment_runtime.py",
    "sc_assignment_watchdog.py",
    "sc_assignment_failover.py",
    "sc_seat_identity.py",
    "sc_seat_pipe.py",
    "sc_seat_revocation.py",
    "sc_authority_trust.py",
    "sc_trust_anchor.py",
)
CI_WORKFLOW = Path(".github/workflows/ci.yml")
INVARIANTS = Path("release/core_invariants.json")
PYPROJECT = Path("pyproject.toml")


@dataclass(frozen=True)
class Finding:
    code: str
    status: str
    detail: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _contains_module(text: str, module: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(module)}(?![A-Za-z0-9_])", text) is not None


def _hash_drift(root: Path, policy_root: Path) -> list[Finding]:
    claims_path = policy_root / "release/claims.json"
    if not claims_path.is_file():
        return []
    try:
        claims = json.loads(_read(claims_path))
    except (OSError, json.JSONDecodeError) as exc:
        return [Finding("claim_hash_drift", "unknown", f"claims unreadable: {exc}")]
    drift: list[str] = []
    for claim in claims.get("claims", []):
        for evidence in claim.get("evidence", []):
            relative = evidence.get("path")
            expected = evidence.get("sha256_text")
            if not relative or not expected:
                continue
            candidate = root / relative
            if not candidate.is_file():
                continue
            import hashlib

            actual = hashlib.sha256(candidate.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()
            if actual != expected:
                drift.append(str(relative))
    if drift:
        return [Finding("claim_hash_drift", "expected_premerge", ", ".join(sorted(set(drift))))]
    return [Finding("claim_hash_drift", "pass", "no recorded evidence hash drift")]


def validate_candidate(root: str | Path, *, policy_root: str | Path | None = None,
                       workflow: str | Path | None = None) -> dict[str, object]:
    candidate = Path(root).resolve()
    policy = Path(policy_root).resolve() if policy_root else candidate
    workflow_path = candidate / (workflow or CI_WORKFLOW)
    findings: list[Finding] = []
    missing = [name for name in REQUIRED_MODULES if not (candidate / name).is_file()]
    findings.append(Finding(
        "implementation.modules", "fail" if missing else "pass",
        "missing=" + ",".join(missing) if missing else f"{len(REQUIRED_MODULES)} required candidate modules present",
    ))

    pyproject = candidate / PYPROJECT
    manifest_text = _read(pyproject) if pyproject.is_file() else ""
    manifest_missing = [name for name in REQUIRED_MODULES if not _contains_module(manifest_text, name)]
    findings.append(Finding(
        "package.manifest", "fail" if manifest_missing else "pass",
        "missing=" + ",".join(manifest_missing) if manifest_missing else "all candidate modules declared",
    ))

    invariant_path = policy / INVARIANTS
    invariant_missing: list[str] = []
    if not invariant_path.is_file():
        findings.append(Finding("release.invariants", "fail", f"missing {invariant_path}"))
    else:
        try:
            invariant_data = json.loads(_read(invariant_path))
            required = set(invariant_data.get("required_wheel_modules", []))
            invariant_missing = [name for name in REQUIRED_MODULES if name not in required]
            findings.append(Finding(
                "release.invariants", "fail" if invariant_missing else "pass",
                "missing=" + ",".join(invariant_missing) if invariant_missing else "all candidate modules required",
            ))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(Finding("release.invariants", "fail", f"unreadable: {exc}"))

    if not workflow_path.is_file():
        findings.append(Finding("ci.workflow", "fail", f"missing {workflow_path}"))
    else:
        workflow_text = _read(workflow_path)
        validator_ref = "tools/candidate_coherence.py"
        has_hook = validator_ref in workflow_text and ("--root" in workflow_text or "--candidate-root" in workflow_text)
        findings.append(Finding(
            "ci.workflow", "pass" if has_hook else "fail",
            "candidate-root validator hook present" if has_hook else "workflow does not invoke candidate-root validator",
        ))

    findings.extend(_hash_drift(candidate, policy))
    implementation_failures = [item for item in findings if item.status == "fail" and item.code != "claim_hash_drift"]
    return {
        "ok": not implementation_failures,
        "root": str(candidate),
        "required_modules": list(REQUIRED_MODULES),
        "findings": [asdict(item) for item in findings],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", "--candidate-root", dest="root", required=True)
    parser.add_argument("--policy-root")
    parser.add_argument("--workflow")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = validate_candidate(args.root, policy_root=args.policy_root, workflow=args.workflow)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
