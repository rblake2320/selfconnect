"""browser_local_proof.py - local browser proof for SelfConnect.

This probe tests browser capability boundaries without touching real sites.
It launches a local HTTP server, opens an isolated Edge/Chrome window, verifies
the browser HWND, reads page state through UIA, fills a normal form on an owned
page, and detects a mock protected checkpoint without bypassing it.

Usage:
    python experiments/win32_probe/browser_local_proof.py --verbose

The probe returns PASS/FAIL/NA and can write a redacted JSON proof artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BROWSER_CLASSES = ("Chrome_WidgetWin_1", "ApplicationFrameWindow")
RESULT_SENTINEL = "SC_BROWSER_RESULT_SUBMITTED"
FORM_SENTINEL = "SC_BROWSER_FORM_READY"
PROTECTED_SENTINEL = "SC_PROTECTED_CHECKPOINT"


class BrowserProofVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "NA"


@dataclass
class BrowserWindowRecord:
    hwnd: int
    pid: int
    exe_name: str
    class_name: str
    title: str


@dataclass
class BrowserProofRecord:
    verdict: BrowserProofVerdict = BrowserProofVerdict.NA
    na_reason: str = ""
    browser_exe: str = ""
    browser_kind: str = ""
    form_hwnd: int = 0
    protected_hwnd: int = 0
    form_pid: int = 0
    protected_pid: int = 0
    form_title_hash: str = ""
    protected_title_hash: str = ""
    nonce_hash: str = ""
    payload_hash: str = ""
    read_method: str = ""
    input_method: str = ""
    form_submitted: bool = False
    protected_detected: bool = False
    route_isolated: bool = False
    capture_ok: bool = False
    latency_ms: float = 0.0
    redacted: bool = False
    notes: list[str] | None = None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def detect_form_ready(text: str, nonce: str) -> bool:
    return FORM_SENTINEL in text and nonce in text


def detect_form_submitted(text: str, payload: str) -> bool:
    return RESULT_SENTINEL in text and payload in text


def detect_protected_state(text: str) -> bool:
    lowered = text.lower()
    return (
        PROTECTED_SENTINEL.lower() in lowered
        or "human verification required" in lowered
        or "captcha" in lowered
    )


def select_browser_window(
    records: list[BrowserWindowRecord],
    *,
    nonce: str,
    browser_exe: str = "",
) -> BrowserWindowRecord | None:
    matches = []
    for record in records:
        if record.class_name not in BROWSER_CLASSES:
            continue
        if browser_exe and record.exe_name.lower() != browser_exe.lower():
            continue
        if nonce.lower() in record.title.lower():
            matches.append(record)
    if not matches:
        return None
    return max(matches, key=lambda item: len(item.title))


def sanitize_record(record: BrowserProofRecord) -> dict[str, object]:
    data = asdict(record)
    data["verdict"] = record.verdict.value
    data["form_hwnd"] = "<redacted>" if record.form_hwnd else ""
    data["protected_hwnd"] = "<redacted>" if record.protected_hwnd else ""
    data["form_pid"] = "<redacted>" if record.form_pid else ""
    data["protected_pid"] = "<redacted>" if record.protected_pid else ""
    data["redacted"] = True
    return data


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


class _ProofHandler(BaseHTTPRequestHandler):
    server_version = "SelfConnectBrowserProof/1.0"

    def log_message(self, *_args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        nonce = params.get("nonce", ["SC_BROWSER_UNKNOWN"])[0]
        if parsed.path == "/protected":
            self._send_html(_protected_page(nonce))
            return
        self._send_html(_form_page(nonce))

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _form_page(nonce: str) -> str:
    safe_nonce = html.escape(nonce)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SC Browser Proof FORM {safe_nonce}</title>
  <style>
    body {{ font-family: Segoe UI, sans-serif; margin: 32px; }}
    input, button {{ font-size: 18px; padding: 8px; }}
  </style>
</head>
<body>
  <main>
    <h1>{FORM_SENTINEL} {safe_nonce}</h1>
    <label for="proof-input">SelfConnect Proof Input</label>
    <input id="proof-input" aria-label="SelfConnect Proof Input" autofocus autocomplete="off">
    <button id="submit-button" aria-label="Submit Proof">Submit Proof</button>
    <p id="result" aria-live="polite">SC_BROWSER_RESULT_EMPTY</p>
  </main>
  <script>
    const input = document.getElementById('proof-input');
    const result = document.getElementById('result');
    const submit = () => {{
      result.textContent = '{RESULT_SENTINEL} ' + input.value;
      document.title = 'SC Browser Proof SUBMITTED {safe_nonce}';
    }};
    document.getElementById('submit-button').addEventListener('click', submit);
    input.addEventListener('keydown', event => {{
      if (event.key === 'Enter') submit();
    }});
    window.addEventListener('load', () => input.focus());
  </script>
</body>
</html>"""


def _protected_page(nonce: str) -> str:
    safe_nonce = html.escape(nonce)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SC Browser Proof PROTECTED {safe_nonce}</title>
</head>
<body>
  <main>
    <h1>{PROTECTED_SENTINEL} {safe_nonce}</h1>
    <p>Human verification required. Automation must pause here.</p>
    <p>Mock CAPTCHA checkpoint for SelfConnect boundary testing.</p>
  </main>
</body>
</html>"""


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, _request, _client_address) -> None:
        return


class LocalProofServer:
    def __init__(self) -> None:
        self.httpd = _QuietThreadingHTTPServer(("127.0.0.1", 0), _ProofHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def start(self) -> LocalProofServer:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def find_browser_executable() -> tuple[str, str]:
    candidates = []
    env_path = os.environ.get("SELFCONNECT_BROWSER_PATH", "").strip()
    if env_path:
        candidates.append(("custom", env_path))
    for name in ("msedge.exe", "chrome.exe"):
        found = shutil.which(name)
        if found:
            candidates.append((Path(found).stem, found))
    candidates.extend([
        ("msedge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ("msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ])
    for kind, path in candidates:
        if Path(path).exists():
            return kind, path
    return "", ""


def _window_records() -> list[BrowserWindowRecord]:
    sys.path.insert(0, str(_repo_root()))
    from self_connect import list_windows  # type: ignore[import]

    records = []
    for window in list_windows():
        records.append(BrowserWindowRecord(
            hwnd=int(window.hwnd),
            pid=int(getattr(window, "pid", 0)),
            exe_name=str(getattr(window, "exe_name", "")),
            class_name=str(getattr(window, "class_name", "")),
            title=str(getattr(window, "title", "")),
        ))
    return records


def wait_for_browser_window(nonce: str, browser_exe: str, timeout_s: float = 20.0) -> BrowserWindowRecord | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        match = select_browser_window(_window_records(), nonce=nonce, browser_exe=browser_exe)
        if match:
            return match
        time.sleep(0.5)
    return None


def read_window_text(hwnd: int) -> tuple[str, str]:
    sys.path.insert(0, str(_repo_root()))
    import sc_cli  # type: ignore[import]

    report = sc_cli.read_window(hwnd, prefer_uia=True)
    return str(report.get("text", "")), str(report.get("method", "none"))


def verify_browser_target(record: BrowserWindowRecord, nonce: str) -> tuple[bool, list[str]]:
    sys.path.insert(0, str(_repo_root()))
    import sc_cli  # type: ignore[import]

    guard = sc_cli.verify_target(
        record.hwnd,
        expected_pid=record.pid,
        expected_exe=record.exe_name,
        expected_class=record.class_name,
        expected_title=nonce,
        allow_classes=BROWSER_CLASSES,
        require_terminal=False,
        require_expectation=True,
    )
    return bool(guard["ok"]), list(guard["reasons"])


def _fill_form_with_uia(hwnd: int, payload: str) -> bool:
    try:
        import pythoncom  # type: ignore
        from pywinauto import Desktop  # type: ignore
    except Exception:
        return False
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        wrapper = Desktop(backend="uia").window(handle=hwnd)
        target = None
        for child in wrapper.descendants():
            name = ""
            control_type = ""
            try:
                name = child.window_text() or ""
                control_type = child.element_info.control_type or ""
            except Exception:
                continue
            if "SelfConnect Proof Input" in name and control_type in {"Edit", "Document"}:
                target = child
                break
        if target is None:
            return False
        target.set_focus()
        try:
            target.set_edit_text(payload)
            target.type_keys("{ENTER}")
        except Exception:
            target.type_keys("^a{BACKSPACE}" + payload + "{ENTER}", with_spaces=True, pause=0.02)
        return True
    except Exception:
        return False
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _fill_form_with_clipboard(hwnd: int, payload: str) -> bool:
    sys.path.insert(0, str(_repo_root()))
    try:
        from self_connect import focus_window, send_keys, write_clipboard  # type: ignore[import]
    except Exception:
        return False
    if not focus_window(hwnd):
        return False
    if not write_clipboard(payload):
        return False
    time.sleep(0.2)
    send_keys("ctrl", "v")
    time.sleep(0.1)
    send_keys("enter")
    return True


def fill_form(hwnd: int, payload: str) -> str:
    if _fill_form_with_uia(hwnd, payload):
        return "uia_value_or_keys"
    if _fill_form_with_clipboard(hwnd, payload):
        return "foreground_clipboard_keys"
    return ""


def close_probe_window(hwnd: int) -> None:
    sys.path.insert(0, str(_repo_root()))
    try:
        from self_connect import close_window  # type: ignore[import]
        close_window(hwnd)
    except Exception:
        pass


def run_probe(*, timeout_s: float = 25.0, output_path: str = "") -> BrowserProofRecord:
    record = BrowserProofRecord(notes=[])
    if sys.platform != "win32":
        record.na_reason = "Win32 platform required"
        return record

    browser_kind, browser_path = find_browser_executable()
    if not browser_path:
        record.na_reason = "No Edge or Chrome executable found; set SELFCONNECT_BROWSER_PATH"
        return record
    record.browser_kind = browser_kind
    record.browser_exe = Path(browser_path).name

    nonce = f"SC_BROWSER_{uuid.uuid4().hex[:8].upper()}"
    protected_nonce = f"{nonce}_PROTECTED"
    payload = f"payload_{uuid.uuid4().hex[:10]}"
    record.nonce_hash = sha256_text(nonce)
    record.payload_hash = sha256_text(payload)

    server = LocalProofServer().start()
    profile_dir = tempfile.mkdtemp(prefix="sc_browser_profile_")
    procs: list[subprocess.Popen] = []
    started = time.time()

    form_url = f"{server.base_url}/form?nonce={nonce}"
    protected_url = f"{server.base_url}/protected?nonce={protected_nonce}"
    common_args = [
        browser_path,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--disable-extensions",
        "--disable-default-apps",
    ]

    try:
        procs.append(subprocess.Popen(common_args + ["--new-window", form_url]))
        form_window = wait_for_browser_window(nonce, record.browser_exe, timeout_s=timeout_s)
        if form_window is None:
            record.na_reason = "Browser form window did not appear"
            return record
        record.form_hwnd = form_window.hwnd
        record.form_pid = form_window.pid
        record.form_title_hash = sha256_text(form_window.title)

        ok, reasons = verify_browser_target(form_window, nonce)
        if not ok:
            record.verdict = BrowserProofVerdict.FAIL
            record.na_reason = f"Browser target guard failed: {', '.join(reasons)}"
            return record

        baseline_text = ""
        method = "none"
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            baseline_text, method = read_window_text(form_window.hwnd)
            record.read_method = method
            if detect_form_ready(baseline_text, nonce):
                break
            time.sleep(0.5)
        if not detect_form_ready(baseline_text, nonce):
            record.na_reason = "UIA/browser readback did not expose local form page text"
            return record

        input_method = fill_form(form_window.hwnd, payload)
        record.input_method = input_method
        if not input_method:
            record.na_reason = "Unable to fill local browser form through UIA or foreground keyboard fallback"
            return record

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            text, method = read_window_text(form_window.hwnd)
            record.read_method = method or record.read_method
            if detect_form_submitted(text, payload):
                record.form_submitted = True
                break
            time.sleep(0.5)
        if not record.form_submitted:
            record.verdict = BrowserProofVerdict.FAIL
            record.na_reason = "Form submission sentinel was not observed in browser readback"
            return record

        procs.append(subprocess.Popen(common_args + ["--new-window", protected_url]))
        protected_window = wait_for_browser_window(protected_nonce, record.browser_exe, timeout_s=timeout_s)
        if protected_window is None:
            record.na_reason = "Protected-state browser window did not appear"
            return record
        record.protected_hwnd = protected_window.hwnd
        record.protected_pid = protected_window.pid
        record.protected_title_hash = sha256_text(protected_window.title)
        record.route_isolated = protected_window.hwnd != form_window.hwnd

        protected_text, _method = read_window_text(protected_window.hwnd)
        record.protected_detected = detect_protected_state(protected_text)
        if not record.protected_detected:
            record.verdict = BrowserProofVerdict.FAIL
            record.na_reason = "Protected checkpoint was not detected in browser readback"
            return record

        try:
            sys.path.insert(0, str(_repo_root()))
            from self_connect import save_capture  # type: ignore[import]
            capture_path = Path(tempfile.gettempdir()) / f"sc_browser_proof_{nonce}.png"
            record.capture_ok = bool(save_capture(form_window.hwnd, str(capture_path), crop=True))
        except Exception:
            record.capture_ok = False

        record.latency_ms = (time.time() - started) * 1000
        record.verdict = BrowserProofVerdict.PASS
        record.notes = [
            "Opened an isolated Edge/Chrome profile against a local owned page.",
            "Verified browser HWND/PID/exe/class/title before interaction.",
            "Read page state through UIA/structured readback.",
            "Filled a normal local form and observed submitted sentinel.",
            "Detected a mock protected checkpoint and did not bypass it.",
        ]
        return record
    finally:
        for hwnd in (record.form_hwnd, record.protected_hwnd):
            if hwnd:
                close_probe_window(hwnd)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass
        time.sleep(0.5)
        server.stop()
        shutil.rmtree(profile_dir, ignore_errors=True)
        if output_path and record.verdict == BrowserProofVerdict.PASS:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(sanitize_record(record), indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SelfConnect local browser proof")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--output", default="", help="write redacted PASS artifact to this path")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    record = run_probe(timeout_s=args.timeout, output_path=args.output)
    reason = f" | {record.na_reason}" if record.na_reason else ""
    print(f"[BROWSER_LOCAL_PROOF] result={record.verdict.value}{reason}")
    if args.verbose or record.verdict != BrowserProofVerdict.PASS:
        print(json.dumps(sanitize_record(record), indent=2, sort_keys=True))
    return 0 if record.verdict == BrowserProofVerdict.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
