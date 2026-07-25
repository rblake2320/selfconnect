"""Produce a real-filesystem M6 shadow compilation and replay proof."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sc_local_agent_runtime import RuntimeConfig, SelfConnectTools  # noqa: E402
from selfconnect_capabilities.broker import CapabilityBroker  # noqa: E402
from selfconnect_capabilities.builtin import BUILTIN_SKILLS  # noqa: E402
from selfconnect_capabilities.evidence import EvidenceStore  # noqa: E402
from selfconnect_capabilities.permissions import Authority  # noqa: E402
from selfconnect_capabilities.registry import SkillRegistry  # noqa: E402
from selfconnect_capabilities.shadow_compiler import ShadowSkillCompiler  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    run_id = uuid.uuid4().hex
    state = output.parent / f"{output.stem}-state-{run_id[:12]}"
    repository = state / "owned-repository"
    repository.mkdir(parents=True)
    registry = SkillRegistry()
    for manifest in BUILTIN_SKILLS:
        registry.register(manifest)
    evidence = EvidenceStore(state / "evidence" / "events.jsonl")
    broker = CapabilityBroker(registry, evidence)
    broker.register_verifier(
        "output-ok",
        lambda arguments, result: {"ok": bool(result.get("ok"))},
    )
    broker.register_adapter(
        "file-read",
        SelfConnectTools(RuntimeConfig(repo_root=repository)).file_read,
    )
    authority = Authority("m6-live-proof", frozenset({"read.file"}))
    compiler = ShadowSkillCompiler(
        state / "compiler",
        registry,
        deterministic_verifiers=frozenset({"output-ok"}),
    )
    digest = registry.get("selfconnect.file-read").digest()
    source_runs = []
    source_paths = []
    started = time.perf_counter()
    for index in range(3):
        path = repository / f"source-{index}.txt"
        path.write_text(f"source run {index} / {run_id}", encoding="utf-8")
        result = broker.execute(
            "selfconnect.file-read",
            {"path": str(path)},
            authority,
            expected_manifest_digest=digest,
        )
        if not result.ok:
            raise RuntimeError(result.as_dict())
        source_runs.append([result.evidence_id])
        source_paths.append(str(path))
    candidate = compiler.compile_from_evidence(
        evidence,
        source_runs,
        name="shadow.read-owned-text",
        version="0.1.0",
        description="Read one owned UTF-8 text file through the guarded repository adapter.",
        compiler_principal="trusted-host-compiler",
    )
    replays = []
    replay_paths = []
    for index in range(3):
        path = repository / f"replay-{index}.txt"
        path.write_text(f"replay run {index} / {run_id}", encoding="utf-8")
        replay_paths.append(str(path))
        replays.append(compiler.replay(
            candidate["candidate_id"],
            {"step_1_path": str(path)},
            broker=broker,
            authority=authority,
        ))
    adversarial = compiler.adversarial_validate(candidate["candidate_id"])
    eligibility = compiler.review_eligibility(candidate["candidate_id"])
    unregistered = False
    try:
        registry.get(candidate["name"])
    except KeyError:
        unregistered = True
    report = {
        "schema": "selfconnect.live-shadow-skill-proof.v1",
        "run_id": run_id,
        "ok": (
            evidence.verify()["ok"]
            and all(replay["ok"] for replay in replays)
            and adversarial["ok"]
            and eligibility["ok"]
            and eligibility["still_shadow"]
            and unregistered
        ),
        "seconds": round(time.perf_counter() - started, 3),
        "real_io": {
            "source_paths": source_paths,
            "replay_paths": replay_paths,
            "source_contents": [Path(path).read_text(encoding="utf-8") for path in source_paths],
            "replay_contents": [Path(path).read_text(encoding="utf-8") for path in replay_paths],
        },
        "evidence_verification": evidence.verify(),
        "candidate": candidate,
        "replays": replays,
        "adversarial": adversarial,
        "eligibility": eligibility,
        "runtime_registry_contains_candidate": not unregistered,
        "approval_created": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
