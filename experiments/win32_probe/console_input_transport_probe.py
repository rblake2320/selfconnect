"""Live, opt-in proof for the ConsoleWindowClass input transport.

The default path creates an isolated cmd.exe console and writes a synthetic
command through the production guarded send path. A PASS requires the target
command interpreter to create an exact-content marker in a temporary directory,
not just Win32 queue/API acceptance or command echo.

No active agent terminal is touched unless the caller supplies an HWND plus
its expected PID, executable, class, and title. Results contain hashes rather
than injected text or screen contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import sc_cli  # noqa: E402
import self_connect as sc  # noqa: E402

CONSOLE_CLASS = "ConsoleWindowClass"


@dataclass
class ConsoleInputProof:
    verified_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    verdict: str = "NA"
    reason: str = ""
    hwnd: int = 0
    pid: int = 0
    exe_name: str = ""
    class_name: str = ""
    title_hash: str = ""
    sentinel_hash: str = ""
    guard_ok: bool = False
    transport: str = ""
    records_requested: int = 0
    records_written: int = 0
    caller_console_restored: bool = False
    transport_accepted: bool = False
    independent_process_effect: bool = False
    delivery_verified: bool = False
    spawned_throwaway: bool = False
    elapsed_ms: float = 0.0
    redacted: bool = True
    sources: list[str] = field(default_factory=list)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _find_window(hwnd: int = 0, title: str = ""):
    deadline = time.time() + 15.0
    while time.time() < deadline:
        for window in sc.list_windows():
            if hwnd and int(window.hwnd) == int(hwnd):
                return window
            if title and window.class_name == CONSOLE_CLASS and title in window.title:
                return window
        time.sleep(0.2)
    return None


def _spawn_throwaway() -> tuple[subprocess.Popen[bytes], str]:
    title = f"SC_CONSOLE_PROBE_{uuid.uuid4().hex[:10].upper()}"
    command = f"title {title} & echo SC_CONSOLE_TARGET_READY"
    process = subprocess.Popen(
        [os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"), "/D", "/K", command],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    return process, title


def _wait_for_marker(path: Path, expected: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if path.is_file() and path.read_text(encoding="utf-8").strip() == expected:
                return True
        except (OSError, UnicodeError):
            pass
        time.sleep(0.2)
    return False


def _sanitize(record: ConsoleInputProof) -> dict[str, object]:
    result = asdict(record)
    result["redacted"] = True
    return result


def run_probe(
    *,
    allow_input: bool,
    hwnd: int = 0,
    expected_pid: int | None = None,
    expected_exe: str = "",
    expected_class: str = "",
    expected_title: str = "",
    timeout_s: float = 10.0,
    output_path: str = "",
) -> ConsoleInputProof:
    record = ConsoleInputProof(
        sources=[
            "Microsoft PostMessageW documentation: return precedes message processing",
            "Microsoft WriteConsoleInput documentation: records are written to a console input buffer",
            "SelfConnect guarded send plus independent target-process marker verification",
        ]
    )
    if sys.platform != "win32":
        record.reason = "Win32 platform required"
        return record
    if not allow_input:
        record.reason = "explicit --allow-input required"
        return record

    started = time.perf_counter()
    process: subprocess.Popen[bytes] | None = None
    title = expected_title
    target = None
    try:
        if hwnd:
            if not all((expected_pid, expected_exe, expected_class, expected_title)):
                record.verdict = "FAIL"
                record.reason = "an external HWND requires pid, exe, class, and title expectations"
                return record
            target = _find_window(hwnd=hwnd)
        else:
            process, title = _spawn_throwaway()
            record.spawned_throwaway = True
            target = _find_window(title=title)

        if target is None:
            record.verdict = "FAIL"
            record.reason = "target window was not found"
            return record

        resolved_pid = int(expected_pid or target.pid)
        resolved_exe = expected_exe or target.exe_name
        resolved_class = expected_class or CONSOLE_CLASS
        resolved_title = title or target.title
        record.hwnd = int(target.hwnd)
        record.pid = int(target.pid)
        record.exe_name = target.exe_name
        record.class_name = target.class_name
        record.title_hash = _sha256(target.title)

        guard = sc_cli.verify_target(
            target.hwnd,
            expected_pid=resolved_pid,
            expected_exe=resolved_exe,
            expected_class=resolved_class,
            expected_title=resolved_title,
            own_pid=os.getpid(),
        )
        record.guard_ok = guard["ok"] is True
        if not record.guard_ok:
            record.verdict = "FAIL"
            record.reason = "target guard denied the current target"
            return record
        if target.class_name != CONSOLE_CLASS:
            record.verdict = "FAIL"
            record.reason = "probe requires ConsoleWindowClass"
            return record

        with tempfile.TemporaryDirectory(prefix="sc-console-proof-") as temporary:
            sentinel = f"SC_CONSOLE_OUTPUT_{uuid.uuid4().hex.upper()}"
            marker = Path(temporary) / "receiver-effect.txt"
            record.sentinel_hash = _sha256(sentinel)
            command = f'>"{marker}" echo {sentinel}'
            delivery = sc_cli.send_text_to_window(
                target.hwnd,
                command,
                submit=True,
                transport="auto",
                allow_input=True,
                expected_pid=resolved_pid,
                expected_exe=resolved_exe,
                expected_class=resolved_class,
                expected_title=resolved_title,
                own_pid=os.getpid(),
            )
            nested = delivery.get("delivery", {}) if isinstance(delivery, dict) else {}
            record.transport = str(delivery.get("transport", "")) if isinstance(delivery, dict) else ""
            record.records_requested = int(nested.get("records_requested", 0))
            record.records_written = int(nested.get("records_written", 0))
            record.caller_console_restored = nested.get("caller_console_restored") is True
            record.transport_accepted = (
                isinstance(delivery, dict)
                and delivery.get("ok") is True
                and record.transport == "win32_console_input"
                and record.records_requested > 0
                and record.records_written == record.records_requested
                and record.caller_console_restored
            )
            if not record.transport_accepted:
                record.verdict = "FAIL"
                record.reason = "console input transport did not produce a complete restored write"
                return record

            record.independent_process_effect = _wait_for_marker(marker, sentinel, timeout_s)
        record.delivery_verified = record.independent_process_effect
        record.verdict = "PASS" if record.delivery_verified else "FAIL"
        record.reason = "" if record.delivery_verified else "target process effect was not observed"
        return record
    finally:
        record.elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        if process is not None:
            if target is not None and process.poll() is None:
                sc.send_string(target, "exit\r", char_delay=0.0)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
        if output_path and record.verdict == "PASS":
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_sanitize(record), indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live ConsoleWindowClass input and readback proof")
    parser.add_argument("--allow-input", action="store_true", help="required explicit input authorization")
    parser.add_argument("--hwnd", type=sc_cli.parse_hwnd, default=0)
    parser.add_argument("--expect-pid", type=int, default=None)
    parser.add_argument("--expect-exe", default="")
    parser.add_argument("--expect-class", default="")
    parser.add_argument("--expect-title", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", default="", help="write a redacted artifact only on PASS")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    record = run_probe(
        allow_input=args.allow_input,
        hwnd=args.hwnd,
        expected_pid=args.expect_pid,
        expected_exe=args.expect_exe,
        expected_class=args.expect_class,
        expected_title=args.expect_title,
        timeout_s=args.timeout,
        output_path=args.output,
    )
    print(
        f"[CONSOLE_INPUT] result={record.verdict} transport={record.transport or 'none'} "
        f"receiver_effect={record.independent_process_effect}"
    )
    if args.verbose or record.verdict != "PASS":
        print(json.dumps(_sanitize(record), indent=2, sort_keys=True))
    return 0 if record.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
