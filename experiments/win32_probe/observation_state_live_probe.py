"""Live, redacted proof for SelfConnect target-bound observation.

This launches real owned Windows UI and terminal processes. It uses no mocks,
monkeypatches, prerecorded screenshots, or simulated adapter results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "owned_observation_window.py"
sys.path.insert(0, str(ROOT))

import sc_cli  # noqa: E402
from experiments.win32_probe.browser_local_proof import (  # noqa: E402
    FORM_SENTINEL,
    LocalProofServer,
    close_probe_window,
    find_browser_executable,
    wait_for_browser_window,
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _ocr_contains(expected: str, actual: str, minimum_ratio: float = 0.9) -> bool:
    expected_key = _normalized(expected)
    actual_key = _normalized(actual)
    if expected_key in actual_key:
        return True
    shortest = max(1, len(expected_key) - 2)
    longest = min(len(actual_key), len(expected_key) + 2)
    return bool(expected_key and actual_key) and any(
        SequenceMatcher(None, expected_key, actual_key[start : start + width]).ratio() >= minimum_ratio
        for width in range(shortest, longest + 1)
        for start in range(0, len(actual_key) - width + 1)
    )


def _terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _launch_fixture(title: str, sentinel: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(FIXTURE),
            "--title",
            title,
            "--sentinel",
            sentinel,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _wait_for_exact(title: str, process: subprocess.Popen, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    result: dict = {}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise RuntimeError(f"owned process exited: stdout={stdout!r} stderr={stderr!r}")
        result = sc_cli.resolve_unique_window(title, exact_title=True)
        if result.get("ok"):
            return result
        time.sleep(0.1)
    raise RuntimeError(f"owned window was not discovered: {result}")


def _terminal_probe() -> tuple[dict, subprocess.Popen | None]:
    target_token = uuid.uuid4().hex[:10].upper()
    content_token = uuid.uuid4().hex[:10].upper()
    title = f"SelfConnect Live Terminal {target_token}"
    sentinel = f"SELFCONNECT TERMINAL LIVE {content_token}"
    command = (
        f"title {title} & color F0 & mode con: cols=120 lines=40 & cls"
        f" & for /L %i in (1,1,24) do @echo {sentinel} LINE %i"
    )
    process = subprocess.Popen(
        ["cmd.exe", "/d", "/q", "/k", command],
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    try:
        resolved = _wait_for_exact(title, process)
        hwnd = int(resolved["window"]["hwnd"])

        resolution_samples = []
        for _index in range(10):
            started = time.perf_counter()
            repeat = sc_cli.resolve_unique_window(title, exact_title=True)
            resolution_samples.append((time.perf_counter() - started) * 1000)
            if not repeat.get("ok") or int(repeat["window"]["hwnd"]) != hwnd:
                raise RuntimeError("exact terminal resolution was not stable")

        adaptive_reports = [
            sc_cli.observe_window(
                hwnd,
                include_text=True,
                include_elements=False,
                ocr_mode="auto",
                max_text_chars=200_000,
            )
            for _index in range(5)
        ]

        try:
            sc_cli._load_sc().focus_window(hwnd)
            time.sleep(0.3)
        except Exception:
            pass
        forced_reports = [
            sc_cli.observe_window(
                hwnd,
                include_text=True,
                include_elements=False,
                ocr_mode="always",
                max_text_chars=200_000,
            )
            for _index in range(3)
        ]
        adaptive = adaptive_reports[-1]
        forced = forced_reports[-1]
        forced_matches = []
        for report in forced_reports:
            forced_ocr_text = report.get("ocr", {}).get("text", "")
            forced_ocr_key = _normalized(forced_ocr_text)
            forced_matches.append(
                _ocr_contains(content_token, forced_ocr_text)
                and "TERMINAL" in forced_ocr_key
                and "LIVE" in forced_ocr_key
            )

        adaptive_ms = median(float(report.get("elapsed_ms", 0.0)) for report in adaptive_reports)
        forced_ms = median(float(report.get("elapsed_ms", 0.0)) for report in forced_reports)
        return {
            "available": True,
            "resolution_ok": True,
            "resolution_p50_ms": round(sorted(resolution_samples)[len(resolution_samples) // 2], 3),
            "resolution_max_ms": round(max(resolution_samples), 3),
            "adaptive_ok": all(bool(report.get("ok")) for report in adaptive_reports),
            "adaptive_samples": len(adaptive_reports),
            "adaptive_method": adaptive.get("text", {}).get("method", ""),
            "adaptive_reason": adaptive.get("accessibility", {}).get("text_assessment", {}).get("reason", ""),
            "adaptive_sentinel_match": all(
                sentinel in report.get("text", {}).get("content", "") for report in adaptive_reports
            ),
            "adaptive_ms": adaptive_ms,
            "forced_ocr_ok": all(bool(report.get("ocr", {}).get("ok")) for report in forced_reports),
            "forced_ocr_samples": len(forced_reports),
            "forced_ocr_sentinel_match": all(forced_matches),
            "forced_ocr_characters": int(forced.get("ocr", {}).get("characters", 0)),
            "forced_capture_route": forced.get("screenshot", {}).get("capture_route", ""),
            "forced_capture_low_information": forced.get("screenshot", {})
            .get("quality", {})
            .get("low_information", True),
            "forced_ocr_ms": forced_ms,
            "adaptive_speedup_vs_forced_ocr_x": (round(forced_ms / adaptive_ms, 3) if adaptive_ms > 0 else 0.0),
            "sentinel_sha256": _sha256(sentinel),
        }, process
    except Exception:
        _terminate(process)
        raise


def _browser_probe() -> dict:
    browser_kind, browser_path = find_browser_executable()
    if not browser_path:
        return {
            "available": False,
            "reason": "Edge or Chrome was not found",
        }

    nonce = f"SC_OBSERVATION_{uuid.uuid4().hex[:10].upper()}"
    server = LocalProofServer().start()
    profile_dir = tempfile.mkdtemp(prefix="sc_observation_browser_")
    process: subprocess.Popen | None = None
    hwnd = 0
    try:
        url = f"{server.base_url}/form?nonce={nonce}"
        process = subprocess.Popen(
            [
                browser_path,
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--disable-extensions",
                "--disable-default-apps",
                "--new-window",
                url,
            ]
        )
        browser_exe = Path(browser_path).name
        window = wait_for_browser_window(
            nonce,
            browser_exe,
            timeout_s=25.0,
        )
        if window is None:
            return {
                "available": True,
                "ok": False,
                "reason": "owned browser window did not appear",
            }
        hwnd = int(window.hwnd)

        started = time.perf_counter()
        resolved = sc_cli.resolve_unique_window(nonce)
        resolve_ms = (time.perf_counter() - started) * 1000
        observed: dict = {}
        content = ""
        readiness_started = time.perf_counter()
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            observed = sc_cli.observe_window(
                hwnd,
                include_text=True,
                include_elements=True,
                ocr_mode="auto",
                max_text_chars=200_000,
            )
            content = observed.get("text", {}).get("content", "")
            if FORM_SENTINEL in content and nonce in content:
                break
            time.sleep(0.2)
        return {
            "available": True,
            "ok": bool(observed.get("ok")),
            "browser_kind": browser_kind,
            "browser_exe": browser_exe,
            "resolution_ok": bool(resolved.get("ok")) and int(resolved["window"]["hwnd"]) == hwnd,
            "resolve_ms": round(resolve_ms, 3),
            "readiness_ms": round(
                (time.perf_counter() - readiness_started) * 1000,
                3,
            ),
            "target_stable": bool(observed.get("target_stable")),
            "method": observed.get("text", {}).get("method", ""),
            "assessment": observed.get("accessibility", {}).get("text_assessment", {}).get("reason", ""),
            "owned_page_sentinel_match": FORM_SENTINEL in content and nonce in content,
            "nonce_sha256": _sha256(nonce),
            "elapsed_ms": observed.get("elapsed_ms", 0.0),
        }
    finally:
        if hwnd:
            close_probe_window(hwnd)
        _terminate(process)
        server.stop()
        shutil.rmtree(profile_dir, ignore_errors=True)


def run_probe(output_path: str = "") -> dict:
    result: dict = {
        "schema": "selfconnect.observation.live-proof.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": sys.platform,
        "real_windows_only": True,
        "uses_mocks": False,
        "redacted": True,
        "checks": {},
    }
    owned: list[subprocess.Popen] = []
    terminal_process: subprocess.Popen | None = None
    if sys.platform != "win32":
        result.update({"status": "NA", "reason": "Win32 is required"})
        return result

    with tempfile.TemporaryDirectory(prefix="sc_observation_live_") as temp_dir:
        token = uuid.uuid4().hex[:10].upper()
        title = f"SelfConnect Observation Proof {token}"
        sentinel = f"SELFCONNECT LIVE OCR {token}"
        first = _launch_fixture(title, sentinel)
        owned.append(first)

        try:
            started = time.perf_counter()
            resolved = _wait_for_exact(title, first)
            resolve_ms = (time.perf_counter() - started) * 1000
            hwnd = int(resolved["window"]["hwnd"])
            screenshot = Path(temp_dir) / "owned-observation.png"
            observed = sc_cli.observe_window(
                hwnd,
                include_text=True,
                include_elements=True,
                include_screenshot=True,
                screenshot_path=str(screenshot),
                ocr_mode="auto",
                max_text_chars=200_000,
            )
            result["app"] = {
                "resolution_ok": bool(resolved.get("ok")),
                "resolve_ms": round(resolve_ms, 3),
                "target_stable": bool(observed.get("target_stable")),
                "observation_method": observed.get("text", {}).get("method", ""),
                "accessibility_assessment": observed.get("accessibility", {})
                .get("text_assessment", {})
                .get("reason", ""),
                "sentinel_match": _ocr_contains(
                    sentinel,
                    observed.get("text", {}).get("content", ""),
                ),
                "sentinel_sha256": _sha256(sentinel),
                "capture_route": observed.get("screenshot", {}).get("capture_route", ""),
                "capture_low_information": observed.get("screenshot", {})
                .get("quality", {})
                .get("low_information", True),
                "screenshot_saved": screenshot.exists(),
                "elapsed_ms": observed.get("elapsed_ms", 0.0),
            }

            prefix = f"SelfConnect Ambiguity Proof {uuid.uuid4().hex[:10].upper()}"
            second = _launch_fixture(f"{prefix} A", f"SELFCONNECT A {token}")
            third = _launch_fixture(f"{prefix} B", f"SELFCONNECT B {token}")
            owned.extend([second, third])
            _wait_for_exact(f"{prefix} A", second)
            _wait_for_exact(f"{prefix} B", third)
            ambiguous = sc_cli.resolve_unique_window(prefix)
            result["ambiguity"] = {
                "rejected": ambiguous.get("reason") == "ambiguous_window",
                "candidate_count": len(ambiguous.get("candidates", [])),
            }

            _terminate(first)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if not sc_cli.resolve_unique_window(title, exact_title=True).get("ok"):
                    break
                time.sleep(0.1)
            closed = sc_cli.observe_window(hwnd, ocr_mode="never")
            result["closed_target"] = {
                "rejected": not closed.get("ok") and "disappeared" in str(closed.get("error", "")),
            }

            result["browser"] = _browser_probe()
            terminal_result, terminal_process = _terminal_probe()
            result["terminal"] = terminal_result

            checks = {
                "unique_resolution": result["app"]["resolution_ok"],
                "target_stable": result["app"]["target_stable"],
                "chrome_only_escalated_to_ocr": (
                    result["app"]["accessibility_assessment"] == "chrome_only"
                    and result["app"]["observation_method"] == "ocr"
                ),
                "real_app_sentinel_read": result["app"]["sentinel_match"],
                "real_pixels_captured": (
                    result["app"]["screenshot_saved"] and not result["app"]["capture_low_information"]
                ),
                "ambiguity_failed_closed": result["ambiguity"]["rejected"],
                "closed_hwnd_failed_closed": result["closed_target"]["rejected"],
                "browser_unique_resolution": result["browser"]["resolution_ok"],
                "browser_target_stable": result["browser"]["target_stable"],
                "browser_owned_page_read": (result["browser"]["ok"] and result["browser"]["owned_page_sentinel_match"]),
                "terminal_unique_resolution": result["terminal"]["resolution_ok"],
                "terminal_accessibility_fast_path": (
                    result["terminal"]["adaptive_ok"]
                    and result["terminal"]["adaptive_reason"] == "rich_text_fast_path"
                    and result["terminal"]["adaptive_sentinel_match"]
                ),
                "terminal_real_ocr": (
                    result["terminal"]["forced_ocr_ok"] and result["terminal"]["forced_ocr_sentinel_match"]
                ),
            }
            result["checks"] = checks
            result["status"] = "PASS" if all(checks.values()) else "FAIL"
        except Exception as exc:
            result["status"] = "FAIL"
            result["reason"] = f"{type(exc).__name__}: {exc}"
        finally:
            for process in reversed(owned):
                _terminate(process)
            _terminate(terminal_process)

    if output_path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real SelfConnect target-bound observation proof",
    )
    parser.add_argument("--output", default="", help="write a redacted JSON artifact")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_probe(args.output)
    print(f"[OBSERVATION_LIVE_PROOF] result={result.get('status', 'FAIL')}")
    if args.verbose or result.get("status") != "PASS":
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
