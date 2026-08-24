from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pytest
import sc_cli

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="live SelfConnect observation tests require Windows",
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "owned_observation_window.py"


@dataclass
class OwnedWindow:
    process: subprocess.Popen
    title: str
    sentinel: str
    hwnd: int

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@pytest.fixture
def owned_window_factory():
    windows: list[OwnedWindow] = []

    def launch(*, title_prefix: str = "SelfConnect Live Observation") -> OwnedWindow:
        token = uuid.uuid4().hex[:10].upper()
        title = f"{title_prefix} {token}"
        sentinel = f"SELFCONNECT LIVE OCR {token}"
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
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
            creationflags=creationflags,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        deadline = time.monotonic() + 20
        resolved = {}
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=2)
                pytest.fail(f"owned observation fixture exited before discovery: stdout={stdout!r} stderr={stderr!r}")
            resolved = sc_cli.resolve_unique_window(title, exact_title=True)
            if resolved.get("ok"):
                break
            time.sleep(0.1)
        if not resolved.get("ok"):
            process.terminate()
            process.wait(timeout=5)
            pytest.fail(f"owned observation fixture was not discovered: {resolved}")

        owned = OwnedWindow(
            process=process,
            title=title,
            sentinel=sentinel,
            hwnd=int(resolved["window"]["hwnd"]),
        )
        windows.append(owned)
        return owned

    yield launch

    for window in reversed(windows):
        window.close()


def _normalized(text: str) -> str:
    return "".join(character for character in text.upper() if character.isalnum())


def _ocr_contains(expected: str, actual: str, minimum_ratio: float = 0.9) -> bool:
    expected_key = _normalized(expected)
    actual_key = _normalized(actual)
    if expected_key in actual_key:
        return True
    if not expected_key or not actual_key:
        return False
    shortest = max(1, len(expected_key) - 2)
    longest = min(len(actual_key), len(expected_key) + 2)
    return any(
        SequenceMatcher(None, expected_key, actual_key[start : start + width]).ratio() >= minimum_ratio
        for width in range(shortest, longest + 1)
        for start in range(0, len(actual_key) - width + 1)
    )


def test_live_unique_resolution_and_adaptive_state(owned_window_factory):
    owned = owned_window_factory()

    resolved = sc_cli.resolve_unique_window(owned.title, exact_title=True)
    observed = sc_cli.observe_window(
        owned.hwnd,
        include_text=True,
        include_elements=True,
        ocr_mode="auto",
        max_text_chars=200_000,
        element_limit=200,
    )

    assert resolved["ok"] is True
    assert resolved["window"]["hwnd"] == owned.hwnd
    assert observed["ok"] is True
    assert observed["target_stable"] is True
    assert observed["surface"] == "classic"
    assert observed["accessibility"]["ok"] is True
    assert observed["accessibility"]["text_assessment"]["reason"] == "chrome_only"
    assert _ocr_contains(owned.sentinel, observed["text"]["content"])
    assert observed["text"]["method"] == "ocr"
    assert observed["screenshot"]["capture_attempted"] is True
    assert observed["screenshot"]["captured"] is True
    assert observed["screenshot"]["quality"]["low_information"] is False
    assert observed["screenshot"]["capture_route"] in {
        "selfconnect_native_capture",
        "imagegrab_foreground_fallback",
    }
    if observed["screenshot"]["capture_route"] == "imagegrab_foreground_fallback":
        assert observed["screenshot"]["fallback_used"] is True


def test_live_capture_and_tesseract_ocr(owned_window_factory, tmp_path):
    pytest.importorskip("pytesseract")
    owned = owned_window_factory()
    screenshot = tmp_path / "owned-observation.png"

    observed = sc_cli.observe_window(
        owned.hwnd,
        include_text=False,
        include_screenshot=True,
        screenshot_path=str(screenshot),
        ocr_mode="always",
        max_text_chars=20_000,
    )

    assert observed["ok"] is True
    assert observed["target_stable"] is True
    assert screenshot.exists()
    assert observed["screenshot"]["captured"] is True
    assert observed["screenshot"]["saved"] is True
    assert observed["screenshot"]["quality"]["near_black"] is False
    assert observed["ocr"]["ok"] is True
    assert _ocr_contains(owned.sentinel, observed["ocr"]["text"])


def test_live_resolution_rejects_two_real_matching_windows(owned_window_factory):
    prefix = f"SelfConnect Ambiguity {uuid.uuid4().hex[:10].upper()}"
    first = owned_window_factory(title_prefix=prefix)
    second = owned_window_factory(title_prefix=prefix)

    resolved = sc_cli.resolve_unique_window(prefix)

    assert resolved["ok"] is False
    assert resolved["reason"] == "ambiguous_window"
    assert {item["hwnd"] for item in resolved["candidates"]} == {
        first.hwnd,
        second.hwnd,
    }


def test_live_observation_rejects_closed_real_window(owned_window_factory):
    owned = owned_window_factory()
    owned.close()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not sc_cli.resolve_unique_window(owned.title, exact_title=True).get("ok"):
            break
        time.sleep(0.1)

    observed = sc_cli.observe_window(owned.hwnd, ocr_mode="never")

    assert observed["ok"] is False
    assert "disappeared" in observed["error"]
