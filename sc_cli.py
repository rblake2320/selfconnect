"""Command line adapter for the SelfConnect package.

This module intentionally wraps the existing flat ``self_connect`` API instead
of replacing it. It gives other machines a stable way to run capability probes,
list targets, read text, capture windows, and optionally send input.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


def _load_sc():
    try:
        import self_connect as sc
    except Exception as exc:  # pragma: no cover - platform/import dependent
        raise RuntimeError(
            "SelfConnect requires Windows with the Win32 API available. "
            f"Import failed: {exc}"
        ) from exc
    return sc


def parse_hwnd(raw: str | int) -> int:
    """Parse decimal or 0x-prefixed HWND values."""
    if isinstance(raw, int):
        return raw
    return int(str(raw).strip(), 0)


def window_to_dict(window: Any) -> dict[str, Any]:
    return {
        "hwnd": int(window.hwnd),
        "pid": int(window.pid),
        "exe_name": window.exe_name,
        "class_name": window.class_name,
        "title": window.title,
    }


def _filter_windows(windows: list[Any], query: str = "", limit: int = 100) -> list[Any]:
    if query:
        needle = query.lower()
        windows = [
            w for w in windows
            if needle in w.title.lower()
            or needle in w.exe_name.lower()
            or needle in w.class_name.lower()
        ]
    return windows[: max(1, limit)]


def list_window_records(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    sc = _load_sc()
    return [window_to_dict(w) for w in _filter_windows(sc.list_windows(), query, limit)]


def find_window_by_hwnd(hwnd: int):
    sc = _load_sc()
    for window in sc.list_windows():
        if int(window.hwnd) == int(hwnd):
            return window
    return None


def doctor_report(include_windows: bool = False, query: str = "", limit: int = 20) -> dict[str, Any]:
    sc = _load_sc()
    windows = sc.list_windows()
    report: dict[str, Any] = {
        "package": "selfconnect",
        "version": getattr(sc, "__version__", "unknown"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "capabilities": dict(getattr(sc, "capabilities", {})),
        "visible_window_count": len(windows),
    }
    if include_windows:
        report["windows"] = [
            window_to_dict(w) for w in _filter_windows(windows, query, limit)
        ]
    return report


def read_window(hwnd: int, prefer_uia: bool = True) -> dict[str, Any]:
    sc = _load_sc()
    hwnd = parse_hwnd(hwnd)
    title = sc.get_window_text(hwnd)
    text = ""
    method = "none"
    child_records: list[dict[str, Any]] = []

    if prefer_uia and sc.capabilities.get("uia_text"):
        try:
            text = sc.get_text_uia(hwnd)
            if text:
                method = "uia_text"
        except Exception as exc:
            child_records.append({"error": f"uia_text failed: {exc}"})

    if not text:
        try:
            children = sc.get_child_texts(hwnd)
            child_records = [
                {"hwnd": int(child_hwnd), "class_name": cls, "text": child_text}
                for child_hwnd, cls, child_text in children
            ]
            text = "\n".join(c["text"] for c in child_records if c["text"])
            method = "child_texts" if text else "none"
        except Exception as exc:
            child_records.append({"error": f"child_texts failed: {exc}"})

    return {
        "hwnd": hwnd,
        "title": title,
        "method": method,
        "text": text,
        "children": child_records,
    }


def capture_window(hwnd: int, path: str = "", crop: bool = True) -> dict[str, Any]:
    sc = _load_sc()
    hwnd = parse_hwnd(hwnd)
    target_path = path or str(Path.cwd() / f"selfconnect_capture_{hwnd}.png")
    saved = sc.save_capture(hwnd, path=target_path, crop=crop)
    return {"ok": bool(saved), "hwnd": hwnd, "path": saved}


def input_allowed(explicit: bool = False, env_name: str = "SELFCONNECT_ALLOW_INPUT") -> bool:
    return explicit or os.environ.get(env_name, "").strip().lower() in {"1", "true", "yes", "on"}


def send_text_to_window(
    hwnd: int,
    text: str,
    *,
    submit: bool = False,
    char_delay: float = 0.05,
    allow_input: bool = False,
    env_name: str = "SELFCONNECT_ALLOW_INPUT",
) -> dict[str, Any]:
    sc = _load_sc()
    hwnd = parse_hwnd(hwnd)
    if not input_allowed(allow_input, env_name=env_name):
        return {
            "ok": False,
            "hwnd": hwnd,
            "error": "input disabled",
            "hint": f"pass --allow-input or set {env_name}=1",
        }

    target = find_window_by_hwnd(hwnd)
    if target is None:
        return {"ok": False, "hwnd": hwnd, "error": "window not found"}

    payload = text + ("\r" if submit else "")
    sc.send_string(target, payload, char_delay=char_delay)
    return {
        "ok": True,
        "hwnd": hwnd,
        "pid": target.pid,
        "exe_name": target.exe_name,
        "title": target.title,
        "chars_sent": len(payload),
    }


def _print_json(data: Any) -> int:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def _print_windows(records: list[dict[str, Any]]) -> int:
    print(f"{'hwnd':>12}  {'pid':<8}  {'exe':<28}  title")
    print("-" * 90)
    for item in records:
        safe_title = item["title"].encode("ascii", "replace").decode()
        print(f"{item['hwnd']:12d}  {item['pid']:<8d}  {item['exe_name']:<28}  {safe_title[:60]}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="selfconnect",
        description="SelfConnect package/probe CLI for Win32 agent experiments.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="print package, platform, and capability probes")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--windows", action="store_true", help="include a window sample")
    p.add_argument("--query", default="", help="filter included windows")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("windows", help="list visible windows")
    p.add_argument("--query", default="", help="filter by title, exe, or class")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true", help="emit JSON")

    p = sub.add_parser("read", help="read text from a window")
    p.add_argument("--hwnd", required=True, type=parse_hwnd)
    p.add_argument("--no-uia", action="store_true", help="skip UIA and use child text only")

    p = sub.add_parser("capture", help="capture a window to a PNG")
    p.add_argument("--hwnd", required=True, type=parse_hwnd)
    p.add_argument("--path", default="")
    p.add_argument("--no-crop", action="store_true")

    p = sub.add_parser("send", help="type text into a target window")
    p.add_argument("--hwnd", required=True, type=parse_hwnd)
    p.add_argument("--text", required=True)
    p.add_argument("--submit", action="store_true", help="append Enter")
    p.add_argument("--char-delay", type=float, default=0.05)
    p.add_argument("--allow-input", action="store_true", help="required unless SELFCONNECT_ALLOW_INPUT=1")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "doctor":
            report = doctor_report(args.windows, args.query, args.limit)
            if args.json:
                return _print_json(report)
            print(f"selfconnect {report['version']} on {report['platform']}")
            print(f"visible windows: {report['visible_window_count']}")
            print("capabilities:")
            for name, enabled in report["capabilities"].items():
                print(f"  {name}: {enabled}")
            if args.windows:
                print()
                return _print_windows(report["windows"])
            return 0

        if args.command == "windows":
            records = list_window_records(args.query, args.limit)
            return _print_json(records) if args.json else _print_windows(records)

        if args.command == "read":
            return _print_json(read_window(args.hwnd, prefer_uia=not args.no_uia))

        if args.command == "capture":
            return _print_json(capture_window(args.hwnd, args.path, crop=not args.no_crop))

        if args.command == "send":
            return _print_json(send_text_to_window(
                args.hwnd,
                args.text,
                submit=args.submit,
                char_delay=args.char_delay,
                allow_input=args.allow_input,
            ))

        parser.error(f"unknown command: {args.command}")
        return 2
    except RuntimeError as exc:
        print(f"selfconnect: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
