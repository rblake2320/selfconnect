"""
Tests for SelfConnect SDK — runs on Windows with a display.

Usage:
    python test_self_connect.py              # run all tests
    python -m pytest test_self_connect.py -v # with pytest
"""

import sys
import time

# All tests require Windows
if sys.platform != "win32":
    print("SKIP: SelfConnect SDK requires Windows")
    sys.exit(0)

from self_connect import (
    __version__, WindowTarget, WindowPool,
    list_windows, find_target, find_child_by_class,
    get_own_terminal_pid, wait_for_window,
    focus_window, get_window_rect,
    move_window, resize_window,
    minimize_window, maximize_window, restore_window,
    send_string, send_keys,
    click_at, click_window, scroll_window,
    read_clipboard, write_clipboard,
    capture_window, crop_to_client, save_capture,
    get_window_text, get_child_texts, get_text_uia,
    wait_for_title_change,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        msg = f" ({detail})" if detail else ""
        print(f"  FAIL  {name}{msg}")


def test_version():
    print("\n-- Version & exports --")
    check("version is string", isinstance(__version__, str))
    check("version not empty", len(__version__) > 0)
    import self_connect
    check("__all__ defined", hasattr(self_connect, "__all__"))
    check("__all__ has 28 exports", len(self_connect.__all__) == 28,
          f"got {len(self_connect.__all__)}")


def test_window_discovery():
    print("\n-- Window discovery --")
    windows = list_windows()
    check("list_windows returns list", isinstance(windows, list))
    check("found at least 1 window", len(windows) >= 1, f"got {len(windows)}")
    if windows:
        w = windows[0]
        check("WindowTarget has hwnd", isinstance(w.hwnd, int))
        check("WindowTarget has title", isinstance(w.title, str))
        check("WindowTarget has pid", isinstance(w.pid, int) and w.pid > 0)
        check("WindowTarget.is_valid()", w.is_valid())
        check("WindowTarget.__str__", "hwnd=" in str(w))

    own = get_own_terminal_pid()
    check("get_own_terminal_pid >= 0", own >= 0, f"got {own}")


def test_find_target():
    print("\n-- find_target --")
    # Should find at least one window with a common keyword
    windows = list_windows()
    if windows:
        # Use first window's title word as keyword
        kw = windows[0].title.split()[0] if windows[0].title.split() else ""
        if kw:
            t = find_target(kw)
            check(f"find_target('{kw[:20]}')", t is not None)
    result = find_target("__NONEXISTENT_WINDOW_XYZZY__")
    check("find_target returns None for missing", result is None)


def test_window_text():
    print("\n-- Window text (zero-inference) --")
    windows = list_windows()
    if windows:
        w = windows[0]
        text = get_window_text(w.hwnd)
        check("get_window_text returns str", isinstance(text, str))
        check("get_window_text matches title", text == w.title)
        children = get_child_texts(w.hwnd)
        check("get_child_texts returns list", isinstance(children, list))
        uia_text = get_text_uia(w.hwnd)
        check("get_text_uia returns str", isinstance(uia_text, str))


def _find_real_window():
    """Find a window with non-zero dimensions for testing."""
    for w in list_windows():
        r = get_window_rect(w.hwnd)
        if r[2] > 0 and r[3] > 0:
            return w
    return None


def test_window_rect():
    print("\n-- Window rect --")
    w = _find_real_window()
    if w:
        r = get_window_rect(w.hwnd)
        check("get_window_rect returns 4-tuple", len(r) == 4)
        x, y, width, height = r
        check("width > 0", width > 0, f"got {width}")
        check("height > 0", height > 0, f"got {height}")
    else:
        check("found real window", False, "no window with dimensions > 0")


def test_clipboard():
    print("\n-- Clipboard --")
    text1 = "Hello from SelfConnect"
    ok1 = write_clipboard(text1)
    check("write_clipboard returns True", ok1)
    result1 = read_clipboard()
    check("clipboard roundtrip", result1 == text1, f"got {result1!r}")

    time.sleep(0.1)
    text2 = f"Timestamp test {time.time()}"
    ok2 = write_clipboard(text2)
    time.sleep(0.05)
    result2 = read_clipboard()
    check("clipboard second write", result2 == text2, f"got {result2!r}")


def test_capture():
    print("\n-- Capture --")
    w = _find_real_window()
    if w:
        img = capture_window(w.hwnd)
        check("capture_window returns image", img is not None)
        if img:
            check("image has size", img.size[0] > 0 and img.size[1] > 0,
                  f"size={img.size}")
            cropped = crop_to_client(w.hwnd, img)
            check("crop_to_client returns image", cropped is not None)
            import os, tempfile
            path = os.path.join(tempfile.gettempdir(), "sc_test.png")
            saved = save_capture(w.hwnd, path=path)
            check("save_capture returns path", saved == path)
            check("save_capture file exists", os.path.exists(path))
            if os.path.exists(path):
                os.unlink(path)


def test_window_pool():
    print("\n-- WindowPool --")
    pool = WindowPool()
    check("empty pool len=0", len(pool) == 0)
    check("empty pool repr", "empty" in repr(pool))

    windows = list_windows()
    if windows:
        w = windows[0]
        pool.add_target("test", w)
        check("add_target len=1", len(pool) == 1)
        check("get returns target", pool.get("test") is w)
        check("status OK", pool.status().get("test") is True)
        pool.remove("test")
        check("remove len=0", len(pool) == 0)


def test_send_keys_import():
    print("\n-- send_keys (import only) --")
    # Can't safely test actual keystrokes, but verify VK resolution
    from self_connect import _resolve_vk
    check("ctrl resolves", _resolve_vk("ctrl") == 0x11)
    check("alt resolves", _resolve_vk("alt") == 0x12)
    check("enter resolves", _resolve_vk("enter") == 0x0D)
    check("f5 resolves", _resolve_vk("f5") == 0x74)
    check("single char 'a'", _resolve_vk("a") > 0)
    try:
        _resolve_vk("__invalid__")
        check("invalid key raises", False)
    except ValueError:
        check("invalid key raises ValueError", True)


def test_wait_for_window():
    print("\n-- wait_for_window --")
    # Should timeout quickly for nonexistent window
    start = time.monotonic()
    result = wait_for_window("__NONEXISTENT__", timeout=1.0, poll=0.2)
    elapsed = time.monotonic() - start
    check("returns None for missing", result is None)
    check("respects timeout", elapsed < 2.0, f"took {elapsed:.1f}s")


if __name__ == "__main__":
    print(f"SelfConnect SDK v{__version__} — Test Suite")
    print("=" * 50)

    test_version()
    test_window_discovery()
    test_find_target()
    test_window_text()
    test_window_rect()
    test_clipboard()
    test_capture()
    test_window_pool()
    test_send_keys_import()
    test_wait_for_window()

    print("\n" + "=" * 50)
    total = PASS + FAIL
    print(f"Results: {PASS}/{total} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)
