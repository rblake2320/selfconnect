from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_import_has_no_spawn_send_or_file_mutation(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    sys.modules.pop("spawn_successor", None)
    module = importlib.import_module("spawn_successor")
    assert module.REJECTION["status"] == "REJECTED"
    assert calls == []


def test_legacy_cli_fails_closed_and_routes_to_migration_v2():
    result = subprocess.run(
        [sys.executable, str(ROOT / "spawn_successor.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "REJECTED"
    assert "unauthenticated" in payload["reason"]
    assert "MigrationCoordinator" in payload["required_path"]
