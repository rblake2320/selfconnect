"""Read-only CLI for Capability Kernel discovery and evidence inspection."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from selfconnect_capabilities import Authority, CapabilityKernel, KernelConfig


def _kernel(state_dir: str = "") -> CapabilityKernel:
    config = KernelConfig.from_env(Path(state_dir).resolve() if state_dir else None)
    if not config.enabled:
        config = KernelConfig(
            enabled=True,
            dynamic_skills=config.dynamic_skills,
            task_graphs=config.task_graphs,
            skill_learning=config.skill_learning,
            state_dir=config.state_dir,
            skill_paths=config.skill_paths,
        )
    permissions = frozenset(
        item.strip()
        for item in os.environ.get(
            "SC_CAPABILITY_CLI_PERMISSIONS",
            "observe.system,read.mesh,read.window,capture.window,read.file",
        ).split(",")
        if item.strip()
    )
    return CapabilityKernel(config, Authority("capability-cli", permissions))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect SelfConnect Capability Kernel")
    parser.add_argument("--state-dir", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    discover = sub.add_parser("discover")
    discover.add_argument("query")
    discover.add_argument("--limit", type=int, default=5)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("capability")
    state = sub.add_parser("state")
    state.add_argument("--prefix", default="")
    state.add_argument("--include-stale", action="store_true")
    state.add_argument("--limit", type=int, default=100)
    changes = sub.add_parser("changes")
    changes.add_argument("--since", type=float, default=0.0)
    changes.add_argument("--limit", type=int, default=100)
    sub.add_parser("verify-evidence")
    args = parser.parse_args(argv)
    kernel = _kernel(args.state_dir)
    if args.command == "list":
        result = {"ok": True, "skills": [item.public_dict() for item in kernel.registry.all()]}
    elif args.command == "discover":
        result = kernel.discover(args.query, args.limit)
    elif args.command == "inspect":
        result = kernel.inspect(args.capability)
    elif args.command == "state":
        result = kernel.world.snapshot(
            prefix=args.prefix,
            include_stale=args.include_stale,
            limit=args.limit,
        )
    elif args.command == "changes":
        result = kernel.world.changes(since=args.since, limit=args.limit)
    else:
        result = kernel.evidence.verify()
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
