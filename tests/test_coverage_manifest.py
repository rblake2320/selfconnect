from __future__ import annotations

import json
from pathlib import Path

import tools.coverage_manifest as coverage_manifest


def test_audit_collects_only_declared_test_modules(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "coverage_manifest.yaml"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "selfconnect.coverage-manifest.v1",
                "invariants": [
                    {
                        "id": invariant_id,
                        "tests": [
                            f"tests/test_invariant_{invariant_id}.py::test_guard"
                        ],
                    }
                    for invariant_id in range(1, 13)
                ],
            }
        ),
        encoding="utf-8",
    )
    observed_paths: list[str] = []

    def fake_collected_nodes(root: Path, test_paths: list[str]) -> set[str]:
        assert root == tmp_path
        observed_paths.extend(test_paths)
        return {
            f"tests/test_invariant_{invariant_id}.py::test_guard"
            for invariant_id in range(1, 13)
        }

    monkeypatch.setattr(coverage_manifest, "collected_nodes", fake_collected_nodes)

    report = coverage_manifest.audit(tmp_path, manifest_path)

    assert report["ok"] is True
    assert observed_paths == sorted(
        [
        f"tests/test_invariant_{invariant_id}.py"
        for invariant_id in range(1, 13)
        ]
    )


def test_empty_manifest_does_not_collect_the_repository(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "coverage_manifest.yaml"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "selfconnect.coverage-manifest.v1",
                "invariants": [],
            }
        ),
        encoding="utf-8",
    )
    observed_paths: list[str] = []

    def fake_collected_nodes(root: Path, test_paths: list[str]) -> set[str]:
        assert root == tmp_path
        observed_paths.extend(test_paths)
        return set()

    monkeypatch.setattr(coverage_manifest, "collected_nodes", fake_collected_nodes)

    report = coverage_manifest.audit(tmp_path, manifest_path)

    assert report["ok"] is False
    assert observed_paths == []
    assert report["missing_ids"] == list(range(1, 13))
