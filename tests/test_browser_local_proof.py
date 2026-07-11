import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).parent.parent / "experiments" / "win32_probe" / "browser_local_proof.py"
_SPEC = importlib.util.spec_from_file_location("browser_local_proof", _MODULE_PATH)
browser_local_proof = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules[_SPEC.name] = browser_local_proof
_SPEC.loader.exec_module(browser_local_proof)


BrowserProofRecord = browser_local_proof.BrowserProofRecord
BrowserProofVerdict = browser_local_proof.BrowserProofVerdict
BrowserWindowRecord = browser_local_proof.BrowserWindowRecord
detect_form_ready = browser_local_proof.detect_form_ready
detect_form_submitted = browser_local_proof.detect_form_submitted
detect_protected_state = browser_local_proof.detect_protected_state
sanitize_record = browser_local_proof.sanitize_record
select_browser_window = browser_local_proof.select_browser_window
sha256_text = browser_local_proof.sha256_text


def test_detect_form_ready_requires_sentinel_and_nonce():
    text = "SC_BROWSER_FORM_READY SC_BROWSER_1234"
    assert detect_form_ready(text, "SC_BROWSER_1234") is True
    assert detect_form_ready(text, "SC_BROWSER_OTHER") is False
    assert detect_form_ready("ordinary page", "SC_BROWSER_1234") is False


def test_detect_form_submitted_requires_payload():
    text = "SC_BROWSER_RESULT_SUBMITTED payload_abc"
    assert detect_form_submitted(text, "payload_abc") is True
    assert detect_form_submitted(text, "payload_xyz") is False


def test_detect_protected_state_matches_mock_checkpoint():
    assert detect_protected_state("SC_PROTECTED_CHECKPOINT nonce") is True
    assert detect_protected_state("Human verification required") is True
    assert detect_protected_state("Mock CAPTCHA checkpoint") is True
    assert detect_protected_state("normal browser page") is False


def test_select_browser_window_by_nonce_and_class():
    records = [
        BrowserWindowRecord(1, 100, "msedge.exe", "Notepad", "SC_BROWSER_123"),
        BrowserWindowRecord(2, 101, "msedge.exe", "Chrome_WidgetWin_1", "SC Browser Proof FORM SC_BROWSER_123"),
        BrowserWindowRecord(3, 102, "chrome.exe", "Chrome_WidgetWin_1", "SC Browser Proof FORM SC_BROWSER_999"),
    ]
    selected = select_browser_window(records, nonce="SC_BROWSER_123", browser_exe="msedge.exe")
    assert selected is not None
    assert selected.hwnd == 2


def test_select_browser_window_returns_none_for_wrong_exe():
    records = [
        BrowserWindowRecord(2, 101, "chrome.exe", "Chrome_WidgetWin_1", "SC Browser Proof FORM SC_BROWSER_123"),
    ]
    assert select_browser_window(records, nonce="SC_BROWSER_123", browser_exe="msedge.exe") is None


def test_select_browser_window_allows_browser_class_without_exe_filter():
    records = [
        BrowserWindowRecord(2, 101, "chrome.exe", "Chrome_WidgetWin_1", "SC Browser Proof FORM SC_BROWSER_123"),
    ]
    selected = select_browser_window(records, nonce="SC_BROWSER_123")
    assert selected is not None
    assert selected.exe_name == "chrome.exe"


def test_sanitize_record_redacts_window_identity():
    record = BrowserProofRecord(
        verdict=BrowserProofVerdict.PASS,
        form_hwnd=1234,
        protected_hwnd=5678,
        form_pid=11,
        protected_pid=22,
        payload_hash=sha256_text("payload"),
    )
    sanitized = sanitize_record(record)
    assert sanitized["verdict"] == "PASS"
    assert sanitized["form_hwnd"] == "<redacted>"
    assert sanitized["protected_hwnd"] == "<redacted>"
    assert sanitized["form_pid"] == "<redacted>"
    assert sanitized["protected_pid"] == "<redacted>"
    assert sanitized["payload_hash"] == sha256_text("payload")
    assert sanitized["redacted"] is True


def test_sanitize_record_keeps_empty_ids_empty():
    sanitized = sanitize_record(BrowserProofRecord())
    assert sanitized["form_hwnd"] == ""
    assert sanitized["protected_hwnd"] == ""
    assert sanitized["form_pid"] == ""
    assert sanitized["protected_pid"] == ""


def test_sha256_text_is_stable():
    assert sha256_text("abc") == sha256_text("abc")
    assert sha256_text("abc") != sha256_text("abcd")
