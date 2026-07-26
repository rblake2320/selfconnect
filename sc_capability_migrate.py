"""Plan or perform a same-user Capability OS state migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from selfconnect_capabilities.evidence import EvidenceStore


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plan_migration(source: Path, destination: Path) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source state directory does not exist: {source}")
    if source == destination or source in destination.parents:
        raise ValueError("destination must be separate from and outside the source")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("destination exists and is not empty")
    evidence_path = source / "evidence.jsonl"
    evidence = (
        EvidenceStore(evidence_path).verify()
        if evidence_path.exists()
        else {"ok": True, "records": 0, "head_hash": "", "absent": True}
    )
    if not evidence["ok"]:
        raise ValueError("source evidence failed authentication")
    files = [
        {
            "path": str(path.relative_to(source)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": _file_digest(path),
        }
        for path in sorted(source.rglob("*"))
        if path.is_file() and not path.name.endswith((".lock", ".tmp"))
    ]
    return {
        "schema": "selfconnect.capability-state-migration-plan.v1",
        "ok": True,
        "source": str(source),
        "destination": str(destination),
        "same_user_required": True,
        "dpapi_key_present": (source / ".integrity_key.dpapi").is_file(),
        "evidence": evidence,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
    }


def apply_migration(source: Path, destination: Path) -> dict[str, Any]:
    plan = plan_migration(source, destination)
    source = Path(plan["source"])
    destination = Path(plan["destination"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.migration-{uuid.uuid4().hex}.tmp"
    if stage.exists():
        raise FileExistsError(f"migration stage unexpectedly exists: {stage}")
    verification: dict[str, Any] = {}
    try:
        shutil.copytree(
            source,
            stage,
            ignore=shutil.ignore_patterns("*.lock", "*.tmp"),
        )
        copied = {
            str(path.relative_to(stage)).replace("\\", "/"): _file_digest(path)
            for path in stage.rglob("*")
            if path.is_file()
        }
        expected = {item["path"]: item["sha256"] for item in plan["files"]}
        if copied != expected:
            raise ValueError("staged migration file digest mismatch")
        staged_evidence = stage / "evidence.jsonl"
        verification = (
            EvidenceStore(staged_evidence).verify()
            if staged_evidence.exists()
            else {"ok": True, "records": 0, "head_hash": "", "absent": True}
        )
        if not verification["ok"] or verification.get("head_hash") != plan["evidence"].get(
            "head_hash"
        ):
            raise ValueError("staged evidence verification failed")
        if destination.exists():
            if any(destination.iterdir()):
                raise FileExistsError("destination became non-empty during migration")
            destination.rmdir()
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {
        **plan,
        "applied": True,
        "post_migration_evidence": verification,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate SelfConnect Capability OS state")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the migration; otherwise emit a read-only plan.",
    )
    args = parser.parse_args(argv)
    operation = apply_migration if args.apply else plan_migration
    try:
        result = operation(Path(args.source), Path(args.destination))
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
