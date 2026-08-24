"""Unified, target-bound SelfConnect window observations.

This module is additive: it composes existing Win32/UIA/capture adapters without
changing any input transport.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def uia_element_records(hwnd: int, limit: int = 200) -> dict[str, Any]:
    """Return a bounded, point-in-time UIA element summary for an HWND."""
    bounded_limit = max(1, min(int(limit), 1000))
    try:
        from pywinauto import Desktop
    except ImportError:
        return {
            "available": False,
            "ok": False,
            "elements": [],
            "error": "pywinauto is not installed; install selfconnect[uia]",
        }

    try:
        wrapper = Desktop(backend="uia").window(handle=hwnd)
        descendants = wrapper.descendants()
        controls = [wrapper, *descendants]
        records: list[dict[str, Any]] = []
        for index, control in enumerate(controls[:bounded_limit]):
            info = getattr(control, "element_info", None)
            rect = getattr(info, "rectangle", None)
            try:
                name = control.window_text() or ""
            except Exception:
                name = str(getattr(info, "name", "") or "")
            try:
                focused = bool(control.has_keyboard_focus())
            except Exception:
                focused = False
            try:
                enabled = bool(control.is_enabled())
            except Exception:
                enabled = None
            try:
                visible = bool(control.is_visible())
            except Exception:
                visible = None
            records.append(
                {
                    "index": index,
                    "name": name,
                    "control_type": str(getattr(info, "control_type", "") or ""),
                    "automation_id": str(getattr(info, "automation_id", "") or ""),
                    "class_name": str(getattr(info, "class_name", "") or ""),
                    "rect": (
                        {
                            "left": int(rect.left),
                            "top": int(rect.top),
                            "right": int(rect.right),
                            "bottom": int(rect.bottom),
                        }
                        if rect is not None
                        else None
                    ),
                    "enabled": enabled,
                    "visible": visible,
                    "focused": focused,
                }
            )
        return {
            "available": True,
            "ok": True,
            "elements": records,
            "count": len(records),
            "truncated": len(controls) > bounded_limit,
            "total_seen": len(controls),
        }
    except Exception as exc:
        return {
            "available": True,
            "ok": False,
            "elements": [],
            "error": f"UIA element enumeration failed: {exc}",
        }


def image_metadata(image: Any) -> dict[str, Any]:
    """Return content and quality metadata without persisting the image."""
    from PIL import ImageStat

    width, height = image.size
    grayscale = image.convert("L")
    extrema = grayscale.getextrema()
    stats = ImageStat.Stat(grayscale)
    mean = float(stats.mean[0]) if stats.mean else 0.0
    stddev = float(stats.stddev[0]) if stats.stddev else 0.0
    digest = hashlib.sha256()
    digest.update(str(image.mode).encode("ascii", errors="replace"))
    digest.update(f"{width}x{height}".encode("ascii"))
    digest.update(image.tobytes())
    return {
        "width": int(width),
        "height": int(height),
        "mode": str(image.mode),
        "sha256": digest.hexdigest(),
        "luminance_min": int(extrema[0]),
        "luminance_max": int(extrema[1]),
        "luminance_mean": round(mean, 3),
        "luminance_stddev": round(stddev, 3),
        "near_black": int(extrema[1]) < 5,
        "low_information": stddev < 2.0,
    }


def ocr_image(image: Any) -> str:
    """Run the optional OCR adapter while keeping its dependency out of core."""
    import pytesseract

    return str(pytesseract.image_to_string(image) or "")


def capture_image(sc: Any, hwnd: int, crop: bool) -> tuple[Any, dict[str, Any]]:
    """Capture with SelfConnect's native path, then a guarded screen fallback.

    The screen fallback is used only when the first frame has very little
    information and the exact target is the foreground window. This avoids
    silently returning pixels from an occluding application.
    """
    primary = sc.capture_window(hwnd)
    if primary is not None and crop:
        primary = sc.crop_to_client(hwnd, primary)
    primary_quality = image_metadata(primary) if primary is not None else {}

    details: dict[str, Any] = {
        "method": "selfconnect_native_capture",
        "foreground_hwnd": 0,
        "fallback_attempted": False,
        "fallback_used": False,
        "fallback_reason": "",
        "primary_quality": primary_quality,
        "quality": primary_quality,
    }
    needs_fallback = bool(
        primary is None or primary_quality.get("near_black") or primary_quality.get("low_information")
    )
    if not needs_fallback:
        return primary, details

    try:
        foreground_hwnd = int(sc.user32.GetForegroundWindow() or 0)
    except Exception:
        foreground_hwnd = 0
    details["foreground_hwnd"] = foreground_hwnd
    if foreground_hwnd != hwnd:
        details["fallback_reason"] = "target_not_foreground"
        return primary, details

    try:
        minimized = bool(sc.user32.IsIconic(hwnd))
    except Exception:
        minimized = False
    if minimized:
        details["fallback_reason"] = "target_minimized"
        return primary, details

    details["fallback_attempted"] = True
    try:
        from PIL import ImageGrab

        x, y, width, height = sc.get_window_rect(hwnd)
        fallback = ImageGrab.grab(
            bbox=(x, y, x + width, y + height),
            all_screens=True,
        )
        if crop:
            fallback = sc.crop_to_client(hwnd, fallback)
        fallback_quality = image_metadata(fallback)
        details["fallback_quality"] = fallback_quality
        primary_stddev = float(primary_quality.get("luminance_stddev", 0.0))
        fallback_stddev = float(fallback_quality.get("luminance_stddev", 0.0))
        if not fallback_quality.get("near_black") and (
            not fallback_quality.get("low_information") or fallback_stddev > primary_stddev + 2.0
        ):
            details.update(
                {
                    "method": "imagegrab_foreground_fallback",
                    "fallback_used": True,
                    "fallback_reason": "primary_frame_low_information",
                    "quality": fallback_quality,
                }
            )
            return fallback, details
        details["fallback_reason"] = "fallback_frame_not_better"
    except Exception as exc:
        details["fallback_reason"] = f"fallback_failed:{type(exc).__name__}"
        details["fallback_error"] = str(exc)
    return primary, details


def same_window_identity(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = ("hwnd", "pid", "exe_name", "class_name")
    return all(str(before.get(key, "")).casefold() == str(after.get(key, "")).casefold() for key in keys)


def accessibility_text_assessment(
    window_title: str,
    text: str,
    element_result: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether UIA exposed app content or only the window frame."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not lines:
        return {"sufficient": False, "reason": "empty"}

    elements = list(element_result.get("elements", []))
    titlebar_bottom = max(
        (
            int(record["rect"]["bottom"])
            for record in elements
            if record.get("control_type") == "TitleBar" and record.get("rect")
        ),
        default=None,
    )
    chrome_names: set[str] = set()
    content_names: list[str] = []
    structural_types = {"Window", "TitleBar", "MenuBar", "MenuItem"}
    for record in elements:
        name = str(record.get("name", "")).strip()
        if not name:
            continue
        rect = record.get("rect") or {}
        control_type = str(record.get("control_type", ""))
        in_titlebar = titlebar_bottom is not None and int(rect.get("top", titlebar_bottom)) < titlebar_bottom
        if control_type in structural_types or in_titlebar:
            chrome_names.add(name.casefold())
        else:
            content_names.append(name)

    title_key = str(window_title).strip().casefold()
    remaining = [line for line in lines if line.casefold() != title_key and line.casefold() not in chrome_names]
    sufficient = bool(content_names or remaining)
    return {
        "sufficient": sufficient,
        "reason": "content_text" if sufficient else "chrome_only",
        "content_name_count": len(content_names),
        "remaining_line_count": len(remaining),
    }


def observe_window(
    hwnd: int,
    *,
    include_text: bool = True,
    include_elements: bool = False,
    include_screenshot: bool = False,
    screenshot_path: str = "",
    crop: bool = True,
    ocr_mode: str = "auto",
    max_text_chars: int = 100_000,
    element_limit: int = 200,
    profile: str = "explore",
    role: str | None = None,
    generation: int | None = None,
    mesh: str = "default",
    owner_sid: str | None = None,
    birth_id: str | None = None,
    lease_table: Any = None,
) -> dict[str, Any]:
    """Build one target-bound observation across terminals, apps, and browsers."""
    import sc_cli

    started = time.perf_counter()
    observed_at = datetime.now(UTC).isoformat()
    hwnd = sc_cli.parse_hwnd(hwnd)
    normalized_ocr_mode = str(ocr_mode).strip().lower()
    if normalized_ocr_mode not in {"auto", "always", "never"}:
        return {
            "ok": False,
            "hwnd": hwnd,
            "error": "ocr_mode must be one of: auto, always, never",
        }

    sc = sc_cli._load_sc()
    target_before = sc_cli.find_window_by_hwnd(hwnd)
    if target_before is None:
        return {
            "ok": False,
            "hwnd": hwnd,
            "error": "window is not visible or disappeared before observation",
        }
    window_before = sc_cli.window_to_dict(target_before)

    read_result: dict[str, Any] = {
        "hwnd": hwnd,
        "method": "none",
        "text": "",
        "children": [],
    }
    governed = sc_cli._is_governed(profile, role, generation)
    if include_text or governed:
        read_result = sc_cli.read_window(
            hwnd,
            prefer_uia=True,
            profile=profile,
            role=role,
            generation=generation,
            mesh=mesh,
            owner_sid=owner_sid,
            birth_id=birth_id,
            lease_table=lease_table,
        )
        if read_result.get("error"):
            return {
                "ok": False,
                "hwnd": hwnd,
                "observed_at": observed_at,
                "window": window_before,
                "error": str(read_result["error"]),
                "read": read_result,
            }

    accessibility_text = str(read_result.get("text", "")) if include_text else ""
    accessibility_lines = [line.strip() for line in accessibility_text.splitlines() if line.strip()]
    rich_text_fast_path = len(accessibility_text.strip()) >= 512 and len(accessibility_lines) >= 5
    adaptive_element_scan = normalized_ocr_mode == "auto" and include_text and not rich_text_fast_path
    element_scan_needed = include_elements or adaptive_element_scan
    element_scan_result = (
        uia_element_records(hwnd, element_limit)
        if element_scan_needed
        else {
            "available": bool(sc.capabilities.get("uia_text")),
            "ok": True,
            "elements": [],
            "count": 0,
        }
    )
    if rich_text_fast_path:
        text_assessment = {
            "sufficient": True,
            "reason": "rich_text_fast_path",
            "characters": len(accessibility_text),
            "nonempty_lines": len(accessibility_lines),
        }
    elif element_scan_needed and element_scan_result.get("ok"):
        text_assessment = accessibility_text_assessment(
            str(window_before.get("title", "")),
            accessibility_text,
            element_scan_result,
        )
    else:
        text_assessment = {
            "sufficient": bool(accessibility_text.strip()),
            "reason": "not_scanned" if accessibility_text.strip() else "empty",
            "characters": len(accessibility_text),
            "nonempty_lines": len(accessibility_lines),
        }

    element_result = dict(element_scan_result)
    element_result["requested"] = bool(include_elements)
    element_result["used_for_adaptive_ocr"] = bool(adaptive_element_scan)
    element_result["text_assessment"] = text_assessment
    if not include_elements:
        element_result["elements"] = []

    ocr_attempted = normalized_ocr_mode == "always" or (
        normalized_ocr_mode == "auto" and include_text and not text_assessment["sufficient"]
    )
    capture_needed = bool(include_screenshot or ocr_attempted)
    image = None
    capture_error = ""
    image_info: dict[str, Any] = {}
    capture_details: dict[str, Any] = {}
    minimized = False
    if capture_needed:
        try:
            minimized = bool(sc.user32.IsIconic(hwnd))
        except Exception:
            minimized = False
        try:
            image, capture_details = capture_image(sc, hwnd, crop)
            image_info = dict(capture_details.get("quality", {}))
            if image is None:
                capture_error = "capture returned no image"
        except Exception as exc:
            capture_error = f"capture failed: {exc}"

    saved_path = ""
    if include_screenshot and image is not None:
        destination = (
            Path(screenshot_path)
            if screenshot_path
            else (Path.cwd() / f"selfconnect_observation_{hwnd}_{int(time.time())}.png")
        )
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination)
            saved_path = str(destination)
        except Exception as exc:
            capture_error = f"screenshot save failed: {exc}"

    ocr_text = ""
    ocr_error = ""
    if ocr_attempted:
        if image is None:
            ocr_error = capture_error or "OCR requires a captured image"
        else:
            try:
                ocr_text = ocr_image(image)
            except ImportError:
                ocr_error = "pytesseract is not installed; install selfconnect[ocr]"
            except Exception as exc:
                ocr_error = f"OCR failed: {exc}"

    prefer_ocr = bool(ocr_text and not text_assessment["sufficient"])
    primary_text = ocr_text if prefer_ocr else accessibility_text or ocr_text
    primary_method = (
        "ocr"
        if prefer_ocr
        else str(read_result.get("method", "none"))
        if accessibility_text
        else "ocr"
        if ocr_text
        else "none"
    )
    bounded_max = max(0, min(int(max_text_chars), 1_000_000))
    visible_text = primary_text[:bounded_max] if bounded_max else ""
    text_sha256 = hashlib.sha256(primary_text.encode("utf-8")).hexdigest()

    target_after = sc_cli.find_window_by_hwnd(hwnd)
    window_after = sc_cli.window_to_dict(target_after) if target_after is not None else {}
    target_stable = bool(target_after is not None and same_window_identity(window_before, window_after))

    degraded_reasons: list[str] = []
    if include_text and not primary_text.strip():
        degraded_reasons.append("no_text_extracted")
    if include_elements and not element_result.get("ok"):
        degraded_reasons.append("uia_elements_unavailable")
    if capture_needed and image is None:
        degraded_reasons.append("capture_failed")
    if ocr_attempted and ocr_error:
        degraded_reasons.append("ocr_failed")
    if image_info.get("near_black"):
        degraded_reasons.append("capture_near_black")
    elif image_info.get("low_information"):
        degraded_reasons.append("capture_low_information")
    if minimized and capture_needed:
        degraded_reasons.append("target_minimized")
    if not target_stable:
        degraded_reasons.append("target_identity_changed")

    observation_basis = {
        "observed_at": observed_at,
        "window": window_before,
        "window_after": window_after,
        "text_sha256": text_sha256,
        "screenshot_sha256": image_info.get("sha256", ""),
    }
    observation_id = hashlib.sha256(json.dumps(observation_basis, sort_keys=True).encode("utf-8")).hexdigest()[:32]
    ok = bool(target_stable and not (include_screenshot and (image is None or not saved_path)))

    try:
        surface = sc.app_type(hwnd)
    except Exception:
        surface = "unknown"

    return {
        "ok": ok,
        "observation_id": observation_id,
        "observed_at": observed_at,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "hwnd": hwnd,
        "window": window_before,
        "window_after": window_after,
        "target_stable": target_stable,
        "title_changed": (str(window_before.get("title", "")) != str(window_after.get("title", ""))),
        "surface": surface,
        "text": {
            "requested": bool(include_text),
            "method": primary_method,
            "content": visible_text,
            "characters": len(primary_text),
            "sha256": text_sha256,
            "truncated": len(primary_text) > len(visible_text),
            "children": read_result.get("children", []),
        },
        "accessibility": element_result,
        "screenshot": {
            "requested": bool(include_screenshot),
            "capture_attempted": capture_needed,
            "captured": image is not None,
            "saved": bool(saved_path),
            "path": saved_path,
            "crop": bool(crop),
            "capture_route": capture_details.get(
                "method",
                "selfconnect_native_capture",
            ),
            "minimized": minimized,
            "foreground_hwnd": capture_details.get("foreground_hwnd", 0),
            "fallback_attempted": capture_details.get("fallback_attempted", False),
            "fallback_used": capture_details.get("fallback_used", False),
            "fallback_reason": capture_details.get("fallback_reason", ""),
            "primary_quality": capture_details.get("primary_quality", {}),
            "quality": image_info,
            "error": capture_error or capture_details.get("fallback_error", ""),
        },
        "ocr": {
            "mode": normalized_ocr_mode,
            "attempted": ocr_attempted,
            "ok": bool(ocr_attempted and not ocr_error),
            "characters": len(ocr_text),
            "sha256": hashlib.sha256(ocr_text.encode("utf-8")).hexdigest(),
            "text": ocr_text[:bounded_max] if bounded_max else "",
            "truncated": len(ocr_text) > bounded_max,
            "error": ocr_error,
        },
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
    }
