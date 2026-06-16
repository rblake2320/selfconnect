"""Service SID helper for SelfConnect daemon experiments.

This script does not implement a Windows service. It wraps the Service Control
Manager commands needed to create/query/delete a service and enable a service
SID for a real service binary or wrapper.

Most mutating commands require an elevated shell.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

DEFAULT_NAME = "SelfConnectProbe"


def _run_sc(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        ["sc.exe", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": ["sc.exe", *args],
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "ok": proc.returncode == 0,
    }


def query(name: str = DEFAULT_NAME) -> dict[str, Any]:
    return {
        "service": _run_sc(["query", name]),
        "sidtype": _run_sc(["qsidtype", name]),
    }


def install(name: str, bin_path: str, *, display_name: str = "", start: str = "demand") -> dict[str, Any]:
    display = display_name or name
    return _run_sc([
        "create",
        name,
        f"binPath= {bin_path}",
        "type= own",
        f"start= {start}",
        f"DisplayName= {display}",
    ])


def set_sid_type(name: str = DEFAULT_NAME, sid_type: str = "restricted") -> dict[str, Any]:
    if sid_type not in {"none", "unrestricted", "restricted"}:
        raise ValueError("sid_type must be one of: none, unrestricted, restricted")
    return _run_sc(["sidtype", name, sid_type])


def delete(name: str = DEFAULT_NAME) -> dict[str, Any]:
    return _run_sc(["delete", name])


def service_sid_name(name: str = DEFAULT_NAME) -> str:
    return rf"NT SERVICE\{name}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SelfConnect Service SID probe helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("query")
    p.add_argument("--name", default=DEFAULT_NAME)

    p = sub.add_parser("install")
    p.add_argument("--name", default=DEFAULT_NAME)
    p.add_argument("--bin-path", required=True)
    p.add_argument("--display-name", default="")
    p.add_argument("--start", default="demand", choices=["demand", "auto", "disabled"])

    p = sub.add_parser("sidtype")
    p.add_argument("--name", default=DEFAULT_NAME)
    p.add_argument("--sid-type", default="restricted", choices=["none", "unrestricted", "restricted"])

    p = sub.add_parser("delete")
    p.add_argument("--name", default=DEFAULT_NAME)

    p = sub.add_parser("sid")
    p.add_argument("--name", default=DEFAULT_NAME)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "query":
        result = query(args.name)
    elif args.command == "install":
        result = install(args.name, args.bin_path, display_name=args.display_name, start=args.start)
    elif args.command == "sidtype":
        result = set_sid_type(args.name, args.sid_type)
    elif args.command == "delete":
        result = delete(args.name)
    elif args.command == "sid":
        result = {"ok": True, "name": args.name, "service_sid": service_sid_name(args.name)}
    else:
        raise AssertionError(args.command)

    print(json.dumps(result, indent=2, sort_keys=True))
    if isinstance(result, dict) and "ok" in result:
        return 0 if result["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
