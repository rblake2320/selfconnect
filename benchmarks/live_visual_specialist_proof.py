"""Live owned-window and model-swap proof for M5."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sc_mesh_registry  # noqa: E402
import self_connect as sc  # noqa: E402
from selfconnect_capabilities.visual_specialist import VisualSpecialist  # noqa: E402

UI_SCRIPT = REPO_ROOT / "tests" / "fixtures" / "native_state_ui.py"


def _ollama(payload: dict) -> dict:
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _stop(model: str) -> None:
    subprocess.run(
        ["ollama", "stop", model],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=30,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--primary-model", default="qwen3.6:27b")
    parser.add_argument("--visual-model", default="qwen3-vl:8b")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    run_id = uuid.uuid4().hex
    state_dir = output.parent / f"{output.stem}-state-{run_id[:12]}"
    state_dir.mkdir(parents=True, exist_ok=True)
    ready = state_dir / "ready.txt"
    state_file = state_dir / "state.txt"
    title = f"SelfConnect Visual Proof {run_id[:10]}"
    role = f"visual-proof-{run_id[:8]}"
    env = dict(os.environ)
    env.update({
        "SC_READY_FILE": str(ready),
        "SC_STATE_FILE": str(state_file),
        "SC_WINDOW_TITLE": title,
    })
    _stop(args.primary_model)
    _stop(args.visual_model)
    process = subprocess.Popen(
        [sys.executable, str(UI_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    started = time.perf_counter()
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not ready.exists():
            time.sleep(0.1)
        if not ready.exists():
            raise RuntimeError("owned visual proof window did not become ready")
        hwnd = int(ready.read_text(encoding="ascii"))
        deadline = time.time() + 10
        window = None
        while time.time() < deadline and window is None:
            window = next(
                (item for item in sc.list_windows() if item.hwnd == hwnd),
                None,
            )
            if window is None:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"owned visual proof process exited with {process.returncode}"
                    )
                time.sleep(0.1)
        if window is None:
            raise RuntimeError("owned visual proof HWND was not enumerated")
        registered = sc_mesh_registry.register_agent(
            hwnd,
            role,
            agent_type="owned_test_ui",
            task="M5 visual specialist state transition",
            allow_non_terminal=True,
            replace=True,
            expected_pid=window.pid,
            expected_exe=window.exe_name,
            expected_class=window.class_name,
            expected_title=window.title,
        )
        if not registered.get("ok"):
            raise RuntimeError(f"owned UI registration failed: {registered}")

        baseline = VisualSpecialist.gpu_snapshot()
        _ollama({
            "model": args.primary_model,
            "prompt": "",
            "stream": False,
            "keep_alive": "5m",
            "options": {"num_ctx": 32_768, "num_predict": 1},
        })
        primary_loaded = VisualSpecialist.gpu_snapshot()
        specialist = VisualSpecialist(
            repo_root=REPO_ROOT,
            primary_model=args.primary_model,
            visual_model=args.visual_model,
        )
        before = specialist.observe_role(role)
        if not before.get("ok"):
            raise RuntimeError(f"before observation failed: {before}")
        clicked = sc.click_button(hwnd, "Advance")
        deadline = time.time() + 10
        while time.time() < deadline and not state_file.exists():
            time.sleep(0.1)
        after = specialist.observe_role(role)
        if not after.get("ok"):
            raise RuntimeError(f"after observation failed: {after}")
        restored_models = specialist.loaded_models()
        ok = (
            before["admission"]["mode"] == "swap_primary_for_visual"
            and after["admission"]["mode"] == "swap_primary_for_visual"
            and "READY" in before["state"].upper()
            and clicked
            and state_file.read_text(encoding="ascii") == "COMPLETE"
            and "COMPLETE" in after["state"].upper()
            and before["visual"].get("untrusted_data") is True
            and after["visual"].get("untrusted_data") is True
            and before["guard"].get("ok") is True
            and after["guard"].get("ok") is True
            and args.primary_model in restored_models
            and args.visual_model not in restored_models
        )
        report = {
            "schema": "selfconnect.live-visual-specialist-proof.v1",
            "run_id": run_id,
            "ok": ok,
            "seconds": round(time.perf_counter() - started, 3),
            "role": role,
            "hwnd": hwnd,
            "target": {
                "pid": window.pid,
                "exe_name": window.exe_name,
                "class_name": window.class_name,
                "title": window.title,
            },
            "models": {
                "primary": args.primary_model,
                "visual": args.visual_model,
                "restored_loaded_models": sorted(restored_models),
            },
            "gpu": {
                "baseline": baseline,
                "primary_loaded": primary_loaded,
                "primary_delta_mb": primary_loaded["used_mb"] - baseline["used_mb"],
            },
            "before": before,
            "action": {
                "method": "semantic Win32 button text via BM_CLICK",
                "label": "Advance",
                "raw_coordinates_used": False,
                "accepted": clicked,
            },
            "after": after,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if ok else 1
    finally:
        if process.poll() is None:
            ctypes_user32 = __import__("ctypes").windll.user32
            if ready.exists():
                ctypes_user32.PostMessageW(int(ready.read_text(encoding="ascii")), 0x0010, 0, 0)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        _stop(args.primary_model)
        _stop(args.visual_model)


if __name__ == "__main__":
    raise SystemExit(main())
