"""Durable local-model role mailbox for SelfConnect.

The visible LOCAL-OLLAMA demos are intentionally short-lived. This module gives
that role a stable mesh identity plus JSONL inbox/outbox files so other agents
can address it even after a demo terminal closes.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import sc_mesh_registry

DEFAULT_ROLE = "local-ollama-1"
DEFAULT_MODEL = "gemma3:latest"
MAX_MESSAGE_CHARS = 4000


def _now() -> float:
    return time.time()


def _slug(value: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip()).strip("-")
    return clean or "local-model"


def default_root() -> Path:
    root = os.environ.get("SELFCONNECT_LOCAL_MODEL_DIR")
    if root:
        return Path(root)
    local = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(local) / "SelfConnect" / "local-model-roles"


def role_dir(role: str = DEFAULT_ROLE, *, root: str | Path | None = None) -> Path:
    return (Path(root) if root else default_root()) / _slug(role)


def paths_for(role: str = DEFAULT_ROLE, *, root: str | Path | None = None) -> dict[str, Path]:
    base = role_dir(role, root=root)
    return {
        "dir": base,
        "state": base / "state.json",
        "inbox": base / "inbox.jsonl",
        "outbox": base / "outbox.jsonl",
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")


def _read_jsonl_with_errors(path: Path, *, limit: int = 50) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    parse_errors = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            parse_errors += 1
            continue
        if isinstance(data, dict):
            rows.append(data)
        else:
            parse_errors += 1
    return rows[-max(1, int(limit)) :], parse_errors


def _read_jsonl(path: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    rows, _ = _read_jsonl_with_errors(path, limit=limit)
    return rows


def ensure_role(
    role: str = DEFAULT_ROLE,
    *,
    model: str = DEFAULT_MODEL,
    mesh: str = sc_mesh_registry.DEFAULT_MESH,
    profile: str = sc_mesh_registry.DEFAULT_PROFILE,
    task: str = "durable local model mailbox",
    status: str = "standby",
    root: str | Path | None = None,
    registry_path: str | Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Create the mailbox files and register a virtual mesh role."""
    paths = paths_for(role, root=root)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    for mailbox in ("inbox", "outbox"):
        paths[mailbox].touch(exist_ok=True)
    prior_state = _read_json(paths["state"])

    endpoint = str(paths["dir"])
    reg = sc_mesh_registry.register_virtual_agent(
        role,
        mesh=mesh,
        agent_type="local_model",
        task=task,
        status=status,
        profile=profile,
        label=role,
        notes="Durable local Ollama role; no live HWND required.",
        transport="mailbox",
        endpoint=endpoint,
        model=model,
        birth_id=str(prior_state.get("birth_id", "")),
        generation=int(prior_state.get("generation", 1) or 1),
        created_at=float(prior_state.get("created_at", _now()) or _now()),
        replace=replace,
        registry_path=registry_path,
    )
    if not reg.get("ok"):
        return {"ok": False, "error": reg.get("error", "registry failed"), "registry": reg}

    agent = reg["agent"]
    state = {
        "role": role,
        "mesh": mesh,
        "profile": profile,
        "model": model,
        "birth_id": agent["birth_id"],
        "generation": agent["generation"],
        "transport": "mailbox",
        "endpoint": endpoint,
        "inbox": str(paths["inbox"]),
        "outbox": str(paths["outbox"]),
        "created_at": prior_state.get("created_at", _now()),
        "updated_at": _now(),
    }
    _write_json(paths["state"], state)
    return {
        "ok": True,
        "state": state,
        "registry": reg,
        "paths": {name: str(path) for name, path in paths.items()},
    }


def write_message(
    role: str,
    *,
    box: str,
    from_role: str,
    to_role: str,
    text: str,
    nonce: str = "",
    kind: str = "coordination",
    root: str | Path | None = None,
) -> dict[str, Any]:
    if box not in {"inbox", "outbox"}:
        return {"ok": False, "error": "box must be inbox or outbox"}
    if not text.strip():
        return {"ok": False, "error": "text is required"}
    paths = paths_for(role, root=root)
    state = _read_json(paths["state"])
    if not state:
        return {
            "ok": False,
            "error": "role not initialized",
            "hint": "run selfconnect-local-model init before writing messages",
            "state_path": str(paths["state"]),
        }
    compact_text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(compact_text) > MAX_MESSAGE_CHARS:
        return {
            "ok": False,
            "error": "message too long",
            "limit": MAX_MESSAGE_CHARS,
            "actual": len(compact_text),
        }
    message_id = f"{_slug(role)}-{uuid.uuid4().hex[:12]}"
    record = {
        "id": message_id,
        "role": role,
        "box": box,
        "from": from_role,
        "to": to_role,
        "kind": kind,
        "nonce": nonce,
        "text": compact_text,
        "created_at": _now(),
        "ack": False,
        "birth_id": state.get("birth_id", ""),
        "generation": state.get("generation", 0),
    }
    _append_jsonl(paths[box], record)
    return {"ok": True, "message": record, "path": str(paths[box])}


def write_inbox(
    role: str,
    *,
    from_role: str,
    text: str,
    nonce: str = "",
    kind: str = "coordination",
    root: str | Path | None = None,
) -> dict[str, Any]:
    return write_message(
        role,
        box="inbox",
        from_role=from_role,
        to_role=role,
        text=text,
        nonce=nonce,
        kind=kind,
        root=root,
    )


def write_outbox(
    role: str,
    *,
    to_role: str,
    text: str,
    nonce: str = "",
    kind: str = "coordination",
    root: str | Path | None = None,
) -> dict[str, Any]:
    return write_message(
        role,
        box="outbox",
        from_role=role,
        to_role=to_role,
        text=text,
        nonce=nonce,
        kind=kind,
        root=root,
    )


def status(role: str = DEFAULT_ROLE, *, root: str | Path | None = None) -> dict[str, Any]:
    paths = paths_for(role, root=root)
    state = _read_json(paths["state"])
    inbox, inbox_errors = _read_jsonl_with_errors(paths["inbox"], limit=10_000)
    outbox, outbox_errors = _read_jsonl_with_errors(paths["outbox"], limit=10_000)
    missing = [name for name in ("state", "inbox", "outbox") if not paths[name].exists()]
    return {
        "ok": bool(state) and not missing,
        "state": state,
        "paths": {name: str(path) for name, path in paths.items()},
        "inbox_count": len(inbox),
        "outbox_count": len(outbox),
        "parse_error_count": inbox_errors + outbox_errors,
        "missing": missing,
    }


def read_box(
    role: str = DEFAULT_ROLE,
    *,
    box: str = "inbox",
    limit: int = 20,
    root: str | Path | None = None,
) -> dict[str, Any]:
    if box not in {"inbox", "outbox"}:
        return {"ok": False, "error": "box must be inbox or outbox"}
    paths = paths_for(role, root=root)
    messages, parse_errors = _read_jsonl_with_errors(paths[box], limit=limit)
    return {"ok": True, "box": box, "messages": messages, "parse_error_count": parse_errors}


def _print_json(data: Any) -> int:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="selfconnect-local-model",
        description="Durable local-model role mailbox for SelfConnect.",
    )
    parser.add_argument("--root", default="", help="override local-model role root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--role", default=DEFAULT_ROLE)
    p.add_argument("--model", default=os.environ.get("SC_LOCAL_MODEL", DEFAULT_MODEL))
    p.add_argument("--mesh", default=sc_mesh_registry.DEFAULT_MESH)
    p.add_argument("--profile", choices=sorted(sc_mesh_registry.VALID_PROFILES), default="explore")
    p.add_argument("--task", default="durable local model mailbox")
    p.add_argument("--status", default="standby")
    p.add_argument("--registry", default="")
    p.add_argument("--replace", action="store_true")

    p = sub.add_parser("inbox")
    p.add_argument("--role", default=DEFAULT_ROLE)
    p.add_argument("--from-role", default="codex-1")
    p.add_argument("--text", required=True)
    p.add_argument("--nonce", default="")
    p.add_argument("--kind", default="coordination")

    p = sub.add_parser("outbox")
    p.add_argument("--role", default=DEFAULT_ROLE)
    p.add_argument("--to-role", default="codex-1")
    p.add_argument("--text", required=True)
    p.add_argument("--nonce", default="")
    p.add_argument("--kind", default="coordination")

    p = sub.add_parser("read")
    p.add_argument("--role", default=DEFAULT_ROLE)
    p.add_argument("--box", choices=("inbox", "outbox"), default="inbox")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("status")
    p.add_argument("--role", default=DEFAULT_ROLE)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = args.root or None

    if args.command == "init":
        return _print_json(ensure_role(
            args.role,
            model=args.model,
            mesh=args.mesh,
            profile=args.profile,
            task=args.task,
            status=args.status,
            root=root,
            registry_path=args.registry or None,
            replace=args.replace,
        ))
    if args.command == "inbox":
        return _print_json(write_inbox(
            args.role,
            from_role=args.from_role,
            text=args.text,
            nonce=args.nonce,
            kind=args.kind,
            root=root,
        ))
    if args.command == "outbox":
        return _print_json(write_outbox(
            args.role,
            to_role=args.to_role,
            text=args.text,
            nonce=args.nonce,
            kind=args.kind,
            root=root,
        ))
    if args.command == "read":
        return _print_json(read_box(args.role, box=args.box, limit=args.limit, root=root))
    if args.command == "status":
        return _print_json(status(args.role, root=root))

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
