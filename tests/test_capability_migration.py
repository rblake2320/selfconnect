"""Real same-user state migration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sc_capability_migrate import apply_migration, plan_migration
from selfconnect_capabilities.evidence import EvidenceStore


def _source_state(root: Path) -> tuple[Path, dict]:
    source = root / "source-state"
    evidence = EvidenceStore(source / "evidence.jsonl")
    evidence.append("migration_real_record", value="owned-state")
    tasks = source / "tasks"
    tasks.mkdir()
    (tasks / "owned.json").write_text('{"status":"owned"}\n', encoding="utf-8")
    return source, evidence.verify()


def test_plan_is_read_only_and_apply_reverifies_real_dpapi_evidence(
    tmp_path: Path,
) -> None:
    source, before = _source_state(tmp_path)
    destination = tmp_path / "destination-state"

    plan = plan_migration(source, destination)
    assert plan["ok"] is True
    assert plan["evidence"] == before
    assert plan["dpapi_key_present"] is True
    assert destination.exists() is False

    applied = apply_migration(source, destination)
    assert applied["applied"] is True
    assert applied["post_migration_evidence"] == before
    assert EvidenceStore(destination / "evidence.jsonl").verify() == before
    assert (destination / "tasks" / "owned.json").read_text(
        encoding="utf-8"
    ) == '{"status":"owned"}\n'


def test_migration_rejects_tampered_source_and_nonempty_destination(
    tmp_path: Path,
) -> None:
    source, _ = _source_state(tmp_path)
    evidence_path = source / "evidence.jsonl"
    evidence_path.write_text(
        evidence_path.read_text(encoding="utf-8").replace("owned-state", "tampered"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="failed authentication"):
        plan_migration(source, tmp_path / "destination")

    clean_source, _ = _source_state(tmp_path / "clean")
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "keep.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not empty"):
        apply_migration(clean_source, destination)
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "do not overwrite"
