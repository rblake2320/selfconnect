"""SelfConnect release truth gate and objective before/after comparator.

The gate deliberately avoids a single quality score. It reports mechanical
release failures, claim/evidence coverage, and comparable benchmark deltas.
Legal conclusions remain outside this tool's scope.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass
class Check:
    check_id: str
    status: str
    detail: str


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _sha256_normalized_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_README_CLAIM_START = re.compile(
    r'^<!-- SC-CLAIM:([a-z][a-z0-9._-]*) START -->$'
)
_README_CLAIM_END = re.compile(
    r'^<!-- SC-CLAIM:([a-z][a-z0-9._-]*) END -->$'
)


def _readme_claim_blocks(text: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Parse explicit public-claim blocks without classifying free-form prose."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    active_id: str | None = None
    active_start = 0
    active_lines: list[str] = []

    for line_no, line in enumerate(lines, 1):
        start = _README_CLAIM_START.fullmatch(line)
        end = _README_CLAIM_END.fullmatch(line)
        if "SC-CLAIM:" in line and not start and not end:
            errors.append(f"line {line_no}: malformed claim tag")
            continue
        if start:
            claim_id = start.group(1)
            if active_id is not None:
                errors.append(
                    f"line {line_no}: nested claim {claim_id!r} inside {active_id!r}"
                )
                continue
            active_id = claim_id
            active_start = line_no
            active_lines = []
            continue
        if end:
            claim_id = end.group(1)
            if active_id is None:
                errors.append(f"line {line_no}: unmatched end tag for {claim_id!r}")
                continue
            if claim_id != active_id:
                errors.append(
                    f"line {line_no}: end tag {claim_id!r} does not match {active_id!r}"
                )
                continue
            content = "\n".join(active_lines).strip("\n") + "\n"
            if claim_id in blocks:
                errors.append(f"line {active_start}: duplicate claim tag {claim_id!r}")
            elif not content.strip():
                errors.append(f"line {active_start}: empty claim block {claim_id!r}")
            else:
                blocks[claim_id] = {
                    "content": content,
                    "sha256_text": _sha256_normalized_text(content),
                    "start_line": str(active_start),
                    "end_line": str(line_no),
                }
            active_id = None
            active_start = 0
            active_lines = []
            continue
        if active_id is not None:
            active_lines.append(line)

    if active_id is not None:
        errors.append(f"line {active_start}: unclosed claim tag {active_id!r}")
    return blocks, errors


def _run(command: list[str], root: Path, timeout: int = 600) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": "\n".join(output.splitlines()[-25:]),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "returncode": -1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": str(exc),
        }


def _git_snapshot(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
        return proc.stdout.strip()

    status = git("status", "--porcelain")
    return {
        "branch": git("branch", "--show-current"),
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(status),
        "dirty_count": len(status.splitlines()) if status else 0,
        "status": status.splitlines()[:20],
    }


def _assignment(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    raise ValueError(f"{name} not found in {path}")


def _dependency_name(spec: str) -> str:
    return re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0].lower()


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _function_parameters(path: Path, function: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            return {arg.arg for arg in args}
    return set()


def _json_value(document: Any, dotted_path: str) -> Any:
    value = document
    for part in dotted_path.split("."):
        if isinstance(value, list):
            value = value[int(part)]
        else:
            value = value[part]
    return value


def _benchmark_summary(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    aggregate = data.get("aggregate", {})
    profiles = data.get("profiles", [])
    replay_accepted = sum(p.get("replay_attempts", {}).get("accepted", 0) for p in profiles)
    stale_accepted = sum(
        p.get("stale_lease_attempts", {}).get("accepted", 0) for p in profiles
    )
    return {
        "path": str(path),
        "sha256_text": _sha256_text(path),
        "run_id": data.get("run_id", ""),
        "transport": data.get("transport", ""),
        "stage": data.get("stage", ""),
        "agent_count": data.get("agent_count"),
        "profile_names": data.get("profile_names", []),
        "messages_per_agent": data.get("messages_per_agent"),
        "logical_message_count": data.get("logical_message_count"),
        "verdict": data.get("verdict", ""),
        "ok": data.get("ok") is True,
        "metrics": {
            "transport_p50_ms": aggregate.get("transport_governance_ms", {}).get("p50"),
            "transport_p95_ms": aggregate.get("transport_governance_ms", {}).get("p95"),
            "transport_p99_ms": aggregate.get("transport_governance_ms", {}).get("p99"),
            "end_to_end_p99_ms": aggregate.get("end_to_end_task_ms", {}).get("p99"),
            "audit_p99_ms": aggregate.get("audit_lag_ms", {}).get("p99"),
            "model_calls_per_known_task": aggregate.get("model_calls_per_known_task"),
            "echo_false_positives": sum(p.get("echo_false_positives", 0) for p in profiles),
            "echo_false_negatives": sum(p.get("echo_false_negatives", 0) for p in profiles),
            "replay_attempts_accepted": replay_accepted,
            "stale_lease_attempts_accepted": stale_accepted,
            "service_errors": sum(len(p.get("service_errors", [])) for p in profiles),
        },
    }


def _audit_claims(
    root: Path,
    claims_doc: dict[str, Any],
    checks: list[Check],
    *,
    readme_text: str | None = None,
) -> dict[str, Any]:
    policy = claims_doc.get("policy", {})
    release_statuses = set(policy.get("release_statuses", []))
    non_release_statuses = set(policy.get("non_release_statuses", []))
    allowed_statuses = release_statuses | non_release_statuses
    seen: set[str] = set()
    public_total = 0
    public_valid = 0
    claim_failures: dict[str, list[str]] = {}
    claim_public_refs: dict[str, dict[str, Any]] = {}
    readme_blocks, readme_errors = _readme_claim_blocks(readme_text or "")

    for claim in claims_doc.get("claims", []):
        claim_id = claim.get("id", "<missing-id>")
        failures: list[str] = []
        if claim_id in seen:
            failures.append("duplicate id")
        seen.add(claim_id)
        status = claim.get("status")
        is_release = claim.get("release") is True
        if status not in allowed_statuses:
            failures.append(f"unknown status {status!r}")
        if is_release:
            public_total += 1
            if status not in release_statuses:
                failures.append(f"release claim has non-release status {status!r}")
        for field in ("statement", "scope", "boundary", "verified_on"):
            if not str(claim.get(field, "")).strip():
                failures.append(f"missing {field}")
        try:
            date.fromisoformat(claim.get("verified_on", ""))
        except (TypeError, ValueError):
            failures.append("verified_on is not an ISO date")

        evidence = claim.get("evidence", [])
        if not evidence:
            failures.append("no evidence")
        for item in evidence:
            relative = item.get("path", "")
            evidence_path = root / relative
            if not relative or not evidence_path.is_file():
                failures.append(f"missing evidence: {relative or '<empty>'}")
                continue
            expected_hash = item.get("sha256_text")
            expected_file_hash = item.get("sha256")
            if expected_hash and expected_file_hash:
                failures.append(f"multiple hash modes: {relative}")
            if expected_hash:
                if not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
                    failures.append(f"invalid sha256_text: {relative}")
                elif _sha256_text(evidence_path) != expected_hash:
                    failures.append(f"hash mismatch: {relative}")
            if expected_file_hash:
                if not re.fullmatch(r"[0-9a-f]{64}", str(expected_file_hash)):
                    failures.append(f"invalid sha256: {relative}")
                elif _sha256(evidence_path) != expected_file_hash:
                    failures.append(f"hash mismatch: {relative}")
            if item.get("assertions"):
                try:
                    document = _read_json(evidence_path)
                except (OSError, json.JSONDecodeError) as exc:
                    failures.append(f"structured evidence unreadable: {relative}: {exc}")
                    continue
                for assertion in item["assertions"]:
                    try:
                        actual = _json_value(document, assertion["path"])
                    except (KeyError, IndexError, TypeError, ValueError):
                        failures.append(
                            f"missing assertion path {relative}:{assertion.get('path')}"
                        )
                        continue
                    if actual != assertion.get("equals"):
                        failures.append(
                            f"assertion failed {relative}:{assertion['path']} "
                            f"expected={assertion.get('equals')!r} actual={actual!r}"
                        )
        public_ref = claim.get("public_readme")
        if public_ref is not None:
            if not isinstance(public_ref, dict):
                failures.append("public_readme must be an object")
            else:
                claim_public_refs[claim_id] = public_ref
                if public_ref.get("tag") != claim_id:
                    failures.append(
                        f"public_readme tag mismatch: {public_ref.get('tag')!r}"
                    )
                if public_ref.get("path") != "README.md":
                    failures.append(
                        f"public_readme path must be README.md: {public_ref.get('path')!r}"
                    )
                block = readme_blocks.get(claim_id)
                if block is None:
                    failures.append("public_readme tag is absent from README.md")
                else:
                    expected_excerpt_hash = public_ref.get("sha256_text", "")
                    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_excerpt_hash)):
                        failures.append("public_readme sha256_text is not a lowercase SHA-256")
                    elif block["sha256_text"] != expected_excerpt_hash:
                        failures.append("public_readme excerpt hash mismatch")
        claim_failures[claim_id] = failures
        if is_release and not failures:
            public_valid += 1
        checks.append(
            Check(
                f"claim.{claim_id}",
                "fail" if failures else "pass",
                "; ".join(failures) if failures else f"{status}; boundary and evidence valid",
            )
        )

    tagged_valid = 0
    tag_failures = list(readme_errors)
    for claim_id in readme_blocks:
        if claim_id not in seen:
            tag_failures.append(f"unregistered README claim tag {claim_id!r}")
            continue
        if claim_id not in claim_public_refs:
            tag_failures.append(
                f"README claim tag {claim_id!r} has no public_readme mapping"
            )
            continue
        if not claim_failures.get(claim_id):
            tagged_valid += 1
    checks.append(
        Check(
            "truth.readme_tagged_claims",
            "fail" if tag_failures else "pass",
            "; ".join(tag_failures)
            if tag_failures
            else (
                f"tagged_valid={tagged_valid}; tagged_total={len(readme_blocks)}; "
                "coverage applies only to explicit SC-CLAIM blocks"
            ),
        )
    )

    return {
        "ledger_claim_total": len(seen),
        "release_ledger_total": public_total,
        "release_ledger_valid": public_valid,
        "release_ledger_coverage_percent": (
            round(100 * public_valid / public_total, 2) if public_total else 100.0
        ),
        "release_ledger_coverage_scope": (
            "Ledger entries with release=true only; this is not README claim coverage."
        ),
        "tagged_readme_valid": tagged_valid,
        "tagged_readme_total": len(readme_blocks),
        "tagged_readme_coverage_percent": (
            round(100 * tagged_valid / len(readme_blocks), 2)
            if readme_blocks
            else 100.0
        ),
        "tagged_readme_coverage_scope": (
            "Explicit SC-CLAIM blocks in README.md only. Numerator is valid tagged "
            "blocks; denominator is all syntactically valid tagged blocks."
        ),
        "natural_language_claim_detection": (
            "PARTIAL: free-form README prose outside explicit SC-CLAIM blocks is not "
            "mechanically classified or counted. Human review remains required."
        ),
        "tag_parse_errors": readme_errors,
    }


def _build_wheel(root: Path, required_modules: list[str]) -> tuple[Check, dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="selfconnect-wheel-") as temp_dir:
        result = _run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", temp_dir], root
        )
        if not result["ok"]:
            return Check("build.wheel", "fail", result["output_tail"]), result
        wheels = list(Path(temp_dir).glob("*.whl"))
        if len(wheels) != 1:
            return Check("build.wheel", "fail", f"expected one wheel, found {len(wheels)}"), result
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
        missing = [name for name in required_modules if name not in names]
        result.update(
            {
                "wheel_name": wheel.name,
                "wheel_sha256": _sha256(wheel),
                "required_modules": len(required_modules),
                "missing_modules": missing,
            }
        )
        if missing:
            return Check("build.wheel", "fail", f"wheel missing: {missing}"), result
        return (
            Check(
                "build.wheel",
                "pass",
                f"{wheel.name}; {len(required_modules)} required modules present",
            ),
            result,
        )


def audit(
    root: Path,
    *,
    policy_root: Path | None = None,
    mode: str = "source",
    allow_dirty: bool = False,
    run_tests: bool = False,
    run_ruff: bool = False,
    build_wheel: bool = False,
    benchmark: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    policy_root = (policy_root or root).resolve()
    checks: list[Check] = []
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    invariants = _read_json(policy_root / "release/core_invariants.json")
    claims_doc = _read_json(policy_root / "release/claims.json")
    repo = _git_snapshot(root)

    if repo["dirty"]:
        checks.append(
            Check(
                "git.clean",
                "warn" if allow_dirty else "fail",
                f"dirty files={repo['dirty_count']}",
            )
        )
    else:
        checks.append(Check("git.clean", "pass", "worktree clean"))

    project = pyproject["project"]
    project_version = project["version"]
    source_version = _assignment(root / "self_connect.py", "__version__")
    readme = (root / "README.md").read_text(encoding="utf-8")
    readme_match = re.search(r"^# SelfConnect SDK v([^\s]+)", readme, re.MULTILINE)
    readme_version = readme_match.group(1) if readme_match else "<missing>"
    versions = {
        "project": project_version,
        "source": source_version,
        "readme": readme_version,
    }
    if len(set(versions.values())) == 1:
        checks.append(Check("identity.source_version", "pass", str(versions)))
    else:
        checks.append(Check("identity.source_version", "fail", str(versions)))

    if mode == "runtime":
        try:
            installed = importlib.metadata.version("selfconnect")
        except importlib.metadata.PackageNotFoundError:
            installed = "<not-installed>"
        versions["installed"] = installed
        checks.append(
            Check(
                "identity.installed_version",
                "pass" if installed == project_version else "fail",
                f"installed={installed}; expected={project_version}",
            )
        )

    classifiers = project.get("classifiers", [])
    license_ok = (
        "Apache License" in (root / "LICENSE").read_text(encoding="utf-8")[:300]
        and "License :: OSI Approved :: Apache Software License" in classifiers
        and "Apache License 2.0" in readme
        and not re.search(r"^MIT\s", readme, re.MULTILINE)
    )
    checks.append(
        Check(
            "identity.license",
            "pass" if license_ok else "fail",
            "LICENSE, classifier, and README must all say Apache License 2.0",
        )
    )

    core_expected = {_dependency_name(dep) for dep in invariants["core_dependencies"]}
    core_actual = {_dependency_name(dep) for dep in project.get("dependencies", [])}
    checks.append(
        Check(
            "boundary.core_dependencies",
            "pass" if core_actual == core_expected else "fail",
            f"expected={sorted(core_expected)} actual={sorted(core_actual)}",
        )
    )
    optional = project.get("optional-dependencies", {})
    for extra, requirements in invariants["optional_dependencies"].items():
        actual = {_dependency_name(dep) for dep in optional.get(extra, [])}
        expected = {_dependency_name(dep) for dep in requirements}
        missing = sorted(expected - actual)
        checks.append(
            Check(
                f"package.extra.{extra}",
                "fail" if missing else "pass",
                f"missing={missing}" if missing else f"contains={sorted(expected)}",
            )
        )

    includes = {
        Path(item).as_posix()
        for item in pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]
    }
    missing_source = [name for name in invariants["required_wheel_modules"] if not (root / name).is_file()]
    missing_include = [name for name in invariants["required_wheel_modules"] if name not in includes]
    module_failures = [
        *(f"missing source:{name}" for name in missing_source),
        *(f"missing include:{name}" for name in missing_include),
    ]
    checks.append(
        Check(
            "package.module_manifest",
            "fail" if module_failures else "pass",
            "; ".join(module_failures)
            if module_failures
            else f"{len(invariants['required_wheel_modules'])} modules declared",
        )
    )

    forbidden = set(invariants["core_network_forbidden_modules"])
    network_failures: list[str] = []
    for relative in invariants["core_network_free_files"]:
        imported = _import_roots(root / relative)
        overlap = sorted(imported & forbidden)
        if overlap:
            network_failures.append(f"{relative}:{overlap}")
    checks.append(
        Check(
            "boundary.core_network_independence",
            "fail" if network_failures else "pass",
            "; ".join(network_failures)
            if network_failures
            else "core actuation files have no network/MCP imports",
        )
    )

    guard = invariants["guarded_input"]
    parameters = _function_parameters(root / guard["file"], guard["function"])
    missing_parameters = sorted(set(guard["required_parameters"]) - parameters)
    checks.append(
        Check(
            "boundary.target_guard",
            "fail" if missing_parameters else "pass",
            f"missing parameters={missing_parameters}"
            if missing_parameters
            else "input gate and target expectations remain explicit",
        )
    )

    prohibited_hits: list[str] = []
    prohibited_phrases = [
        phrase.lower() for phrase in invariants.get("prohibited_release_phrases", [])
    ]
    for relative in invariants.get("release_claim_files", []):
        claim_text = (root / relative).read_text(encoding="utf-8").lower()
        prohibited_hits.extend(
            f"{relative}:{phrase}" for phrase in prohibited_phrases if phrase in claim_text
        )
    checks.append(
        Check(
            "truth.public_wording",
            "fail" if prohibited_hits else "pass",
            "; ".join(prohibited_hits)
            if prohibited_hits
            else "no prohibited absolute patent/evidence wording in release claim files",
        )
    )

    freeze_text = (root / "docs/PATENT_EVIDENCE_FREEZE_2026-06-20.md").read_text(
        encoding="utf-8"
    )
    stale_recovery_claim = "Queued mailbox payload recovery after restart is not yet reduced"
    checks.append(
        Check(
            "truth.no_known_recovery_contradiction",
            "fail" if stale_recovery_claim in freeze_text else "pass",
            "freeze document contains a stale recovery non-claim"
            if stale_recovery_claim in freeze_text
            else "known recovery contradiction absent",
        )
    )

    claims = _audit_claims(root, claims_doc, checks, readme_text=readme)
    command_results: dict[str, Any] = {}
    if run_tests:
        command_results["tests"] = _run([sys.executable, "-m", "pytest", "-q"], root)
        tests = command_results["tests"]
        checks.append(
            Check(
                "quality.tests",
                "pass" if tests["ok"] else "fail",
                f"exit={tests['returncode']} duration={tests['duration_seconds']}s; "
                f"{tests['output_tail']}",
            )
        )
    if run_ruff:
        ruff_targets = [
            *invariants["required_wheel_modules"],
            "claudego",
        ]
        ruff_targets.extend(
            relative
            for relative in ("tools/release_gate.py", "tests/test_release_gate.py")
            if (root / relative).exists()
        )
        command_results["ruff"] = _run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--config",
                str(root / "pyproject.toml"),
                *ruff_targets,
            ],
            root,
        )
        command_results["ruff"]["scope"] = "release package and release-gate files"
        ruff = command_results["ruff"]
        checks.append(
            Check(
                "quality.ruff",
                "pass" if ruff["ok"] else "fail",
                f"scope={ruff['scope']}; exit={ruff['returncode']} "
                f"duration={ruff['duration_seconds']}s; "
                f"{ruff['output_tail']}",
            )
        )
    if build_wheel:
        wheel_check, wheel_result = _build_wheel(root, invariants["required_wheel_modules"])
        checks.append(wheel_check)
        command_results["wheel"] = wheel_result

    benchmark_summary = None
    if benchmark:
        benchmark_path = benchmark if benchmark.is_absolute() else root / benchmark
        if benchmark_path.is_file():
            benchmark_summary = _benchmark_summary(benchmark_path)
            checks.append(
                Check(
                    "benchmark.artifact",
                    "pass" if benchmark_summary["ok"] else "fail",
                    f"{benchmark_summary['transport']} verdict={benchmark_summary['verdict']}",
                )
            )
        else:
            checks.append(Check("benchmark.artifact", "fail", f"missing {benchmark_path}"))

    counts = {
        status: sum(check.status == status for check in checks)
        for status in ("pass", "warn", "fail")
    }
    return {
        "schema_version": 1,
        "ok": counts["fail"] == 0,
        "mode": mode,
        "root": str(root),
        "policy_root": str(policy_root),
        "repo": repo,
        "versions": versions,
        "checks": [asdict(check) for check in checks],
        "counts": counts,
        "claims": claims,
        "benchmark": benchmark_summary,
        "commands": command_results,
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "baseline_commit": baseline.get("repo", {}).get("commit", ""),
        "candidate_commit": candidate.get("repo", {}).get("commit", ""),
        "release_gate": {
            "baseline_failures": baseline.get("counts", {}).get("fail"),
            "candidate_failures": candidate.get("counts", {}).get("fail"),
            "failure_delta": (
                candidate.get("counts", {}).get("fail", 0)
                - baseline.get("counts", {}).get("fail", 0)
            ),
            "baseline_release_ledger_coverage_percent": baseline.get("claims", {}).get(
                "release_ledger_coverage_percent"
            ),
            "candidate_release_ledger_coverage_percent": candidate.get("claims", {}).get(
                "release_ledger_coverage_percent"
            ),
            "baseline_tagged_readme_coverage_percent": baseline.get("claims", {}).get(
                "tagged_readme_coverage_percent"
            ),
            "candidate_tagged_readme_coverage_percent": candidate.get("claims", {}).get(
                "tagged_readme_coverage_percent"
            ),
        },
        "benchmark": None,
    }
    before = baseline.get("benchmark")
    after = candidate.get("benchmark")
    if before and after:
        dimensions = ("transport", "agent_count", "profile_names", "messages_per_agent")
        mismatches = {
            key: {"baseline": before.get(key), "candidate": after.get(key)}
            for key in dimensions
            if before.get(key) != after.get(key)
        }
        benchmark_result: dict[str, Any] = {
            "comparable": not mismatches,
            "mismatches": mismatches,
            "correctness_regression": False,
            "metric_deltas": {},
        }
        before_metrics = before.get("metrics", {})
        after_metrics = after.get("metrics", {})
        correctness_keys = (
            "echo_false_positives",
            "echo_false_negatives",
            "replay_attempts_accepted",
            "stale_lease_attempts_accepted",
            "service_errors",
        )
        benchmark_result["correctness_regression"] = any(
            (after_metrics.get(key) or 0) > (before_metrics.get(key) or 0)
            for key in correctness_keys
        ) or (before.get("ok") is True and after.get("ok") is not True)
        if not mismatches:
            for key, before_value in before_metrics.items():
                after_value = after_metrics.get(key)
                if isinstance(before_value, (int, float)) and isinstance(
                    after_value, (int, float)
                ):
                    improvement = None
                    if before_value != 0:
                        improvement = round(100 * (before_value - after_value) / before_value, 3)
                    benchmark_result["metric_deltas"][key] = {
                        "baseline": before_value,
                        "candidate": after_value,
                        "delta": round(after_value - before_value, 6),
                        "improvement_percent": improvement,
                    }
        result["benchmark"] = benchmark_result
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit and compare SelfConnect releases")
    sub = parser.add_subparsers(dest="command", required=True)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--root", type=Path, default=Path.cwd())
    audit_parser.add_argument(
        "--policy-root",
        type=Path,
        help="load release invariants and claim policy from a different checkout",
    )
    audit_parser.add_argument("--mode", choices=("source", "runtime"), default="source")
    audit_parser.add_argument("--allow-dirty", action="store_true")
    audit_parser.add_argument("--run-tests", action="store_true")
    audit_parser.add_argument("--run-ruff", action="store_true")
    audit_parser.add_argument("--build-wheel", action="store_true")
    audit_parser.add_argument("--benchmark", type=Path)
    audit_parser.add_argument("--output", type=Path)
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("baseline", type=Path)
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument("--output", type=Path)
    return parser


def _emit(document: dict[str, Any], output: Path | None = None) -> None:
    text = json.dumps(document, indent=2, sort_keys=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "audit":
        report = audit(
            args.root,
            policy_root=args.policy_root,
            mode=args.mode,
            allow_dirty=args.allow_dirty,
            run_tests=args.run_tests,
            run_ruff=args.run_ruff,
            build_wheel=args.build_wheel,
            benchmark=args.benchmark,
        )
        _emit(report, args.output)
        return 0 if report["ok"] else 1
    report = compare(_read_json(args.baseline), _read_json(args.candidate))
    _emit(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
