"""Verify that every architectural invariant maps to collected pytest nodes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def collected_nodes(root: Path) -> set[str]:
    result = subprocess.run(
        ["python", "-m", "pytest", "--collect-only", "-q"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pytest collection failed:\n{result.stdout}\n{result.stderr}")
    return {
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if "::" in line and not line.startswith((" ", "="))
    }


def audit(root: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "selfconnect.coverage-manifest.v1":
        raise ValueError("unsupported coverage manifest schema")
    nodes = collected_nodes(root)
    unresolved = []
    duplicate_ids = []
    seen_ids = set()
    for invariant in manifest.get("invariants", []):
        invariant_id = invariant.get("id")
        if invariant_id in seen_ids:
            duplicate_ids.append(invariant_id)
        seen_ids.add(invariant_id)
        tests = invariant.get("tests", [])
        if not tests:
            unresolved.append({"id": invariant_id, "reason": "no tests declared"})
            continue
        missing = [node for node in tests if node.replace("\\", "/") not in nodes]
        if missing:
            unresolved.append({"id": invariant_id, "missing": missing})
    expected_ids = set(range(1, 13))
    missing_ids = sorted(expected_ids - seen_ids)
    extra_ids = sorted(seen_ids - expected_ids)
    return {
        "ok": not unresolved and not duplicate_ids and not missing_ids and not extra_ids,
        "invariants": len(manifest.get("invariants", [])),
        "collected_nodes": len(nodes),
        "unresolved": unresolved,
        "duplicate_ids": duplicate_ids,
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=Path("coverage_manifest.yaml"))
    parser.add_argument("--unresolved", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = args.manifest
    if not manifest.is_absolute():
        manifest = root / manifest
    report = audit(root, manifest)
    if args.unresolved:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"coverage manifest: {report['invariants']} invariants; "
            f"{len(report['unresolved'])} unresolved"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
