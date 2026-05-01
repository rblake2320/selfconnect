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
    # v0.8.0
    PeerState, PeerRecord, AgentRegistry, WatchdogLoop, ApprovalRelay,
    # v0.9.0
    Checkpoint, write_checkpoint, read_checkpoint, MigrationCoordinator,
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
    check("__all__ has 60 exports", len(self_connect.__all__) == 60,
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


def test_layer4_continuity():
    """Layer 4 Continuity — Checkpoint, write_checkpoint, read_checkpoint, MigrationCoordinator."""
    import os, tempfile
    print("\n-- Layer 4 Continuity (v0.9.0) --")

    # Checkpoint dataclass
    cp = Checkpoint(
        role="A",
        own_hwnd=2820438,
        peers=[{"hwnd": 3546648, "label": "B", "state": "READY"}],
        pending={"task": "build v0.9.0"},
        meta={"session": 9},
    )
    check("Checkpoint schema default", cp.schema == "selfconnect-checkpoint-v1")
    check("Checkpoint role", cp.role == "A")
    check("Checkpoint own_hwnd", cp.own_hwnd == 2820438)
    check("Checkpoint peers list", len(cp.peers) == 1)
    check("Checkpoint written_at float", isinstance(cp.written_at, float) and cp.written_at > 0)

    # write_checkpoint + read_checkpoint roundtrip
    tmp = os.path.join(tempfile.gettempdir(), "sc_test_checkpoint.json")
    try:
        saved_path = write_checkpoint(cp, tmp)
        check("write_checkpoint returns path", saved_path == os.path.abspath(tmp))
        check("checkpoint file exists", os.path.exists(saved_path))

        cp2 = read_checkpoint(saved_path)
        check("read_checkpoint role", cp2.role == cp.role)
        check("read_checkpoint own_hwnd", cp2.own_hwnd == cp.own_hwnd)
        check("read_checkpoint peers", cp2.peers == cp.peers)
        check("read_checkpoint pending", cp2.pending == cp.pending)
        check("read_checkpoint schema", cp2.schema == "selfconnect-checkpoint-v1")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    # read_checkpoint with wrong schema
    bad_path = os.path.join(tempfile.gettempdir(), "sc_bad_checkpoint.json")
    import json
    with open(bad_path, "w") as f:
        json.dump({"schema": "wrong", "role": "X", "own_hwnd": 0,
                   "peers": [], "pending": {}, "meta": {}, "written_at": 0.0}, f)
    try:
        read_checkpoint(bad_path)
        check("wrong schema raises ValueError", False)
    except ValueError:
        check("wrong schema raises ValueError", True)
    finally:
        if os.path.exists(bad_path):
            os.unlink(bad_path)

    # MigrationCoordinator — no-op below threshold
    reg = AgentRegistry()
    coord = MigrationCoordinator(own_hwnd=2820438, role="A", registry=reg,
                                  checkpoint_path=os.path.join(tempfile.gettempdir(),
                                                               "sc_test_mc.json"),
                                  capacity=100, threshold=0.70)
    fired = []
    coord.on_migrate(lambda cp, path: fired.append((cp.role, path)))
    result = coord.tick(current=50)
    check("tick below threshold returns False", result is False)
    check("no migration below threshold", not coord.has_migrated)
    check("on_migrate not called below threshold", len(fired) == 0)

    # MigrationCoordinator — triggers at threshold
    coord2 = MigrationCoordinator(own_hwnd=2820438, role="B", registry=reg,
                                   checkpoint_path=os.path.join(tempfile.gettempdir(),
                                                                "sc_test_mc2.json"),
                                   capacity=100, threshold=0.70)
    fired2 = []
    coord2.on_migrate(lambda cp, path: fired2.append((cp.role, path)))
    result2 = coord2.tick(current=75, pending={"status": "migrating"}, meta={"sess": 9})
    check("tick at threshold returns True", result2 is True)
    check("has_migrated True after trigger", coord2.has_migrated)
    check("on_migrate fired once", len(fired2) == 1)
    check("on_migrate got correct role", fired2[0][0] == "B")
    cp_path = fired2[0][1]
    check("checkpoint file written", os.path.exists(cp_path))
    if os.path.exists(cp_path):
        cp_read = read_checkpoint(cp_path)
        check("checkpoint role correct", cp_read.role == "B")
        check("checkpoint pending preserved", cp_read.pending == {"status": "migrating"})
        os.unlink(cp_path)

    # Second tick after migration is idempotent
    result3 = coord2.tick(current=90)
    check("second tick after migration returns False", result3 is False)
    check("on_migrate not called twice", len(fired2) == 1)

    # Clean up mc.json if it exists
    mc1 = os.path.join(tempfile.gettempdir(), "sc_test_mc.json")
    if os.path.exists(mc1):
        os.unlink(mc1)


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
    test_layer4_continuity()

    print("\n" + "=" * 50)
    total = PASS + FAIL
    print(f"Results: {PASS}/{total} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)
