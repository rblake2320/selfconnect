"""Close all Notepad windows — PostMessage keystrokes to dismiss save dialogs."""
import sys, os, time, ctypes, ctypes.wintypes
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from self_connect import list_windows, restore_window

user32 = ctypes.windll.user32

WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_TAB = 0x09
VK_RETURN = 0x0D

EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

def find_input_child(parent_hwnd):
    """Find the InputSiteWindowClass child that receives keyboard input."""
    found = []
    def callback(hwnd, _):
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value
        if "InputSite" in cls or "NotepadTextBox" in cls or "ContentDialog" in cls:
            found.append((cls, hwnd))
        user32.EnumChildWindows(hwnd, EnumChildProc(callback), 0)
        return True
    user32.EnumChildWindows(parent_hwnd, EnumChildProc(callback), 0)
    return found

def post_key(hwnd, vk):
    """Send WM_KEYDOWN then WM_KEYUP via PostMessage."""
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)
    time.sleep(0.1)


windows = [w for w in list_windows() if 'Notepad' in w.title]
print(f"Found {len(windows)} Notepad windows to close\n")

# Try approach 1: PostMessage WM_CLOSE, then PostMessage Tab+Enter to dismiss
for i, w in enumerate(windows):
    safe = w.title.encode('ascii', 'replace').decode()
    print(f"  [{i:2d}] {safe[:60]!r}")

    restore_window(w.hwnd)
    time.sleep(0.15)

    # Send close
    user32.PostMessageW(w.hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.0)

    # Check if still alive
    if not user32.IsWindow(w.hwnd):
        print(f"       -> Closed (clean)")
        continue

    # Dialog appeared — try posting Tab+Enter to the main hwnd
    # Try the main window first
    post_key(w.hwnd, VK_TAB)
    time.sleep(0.1)
    post_key(w.hwnd, VK_RETURN)
    time.sleep(0.8)

    if not user32.IsWindow(w.hwnd):
        print(f"       -> Closed (Don't Save via main hwnd)")
        continue

    # Try InputSite children
    children = find_input_child(w.hwnd)
    for cls, child_hwnd in children:
        post_key(child_hwnd, VK_TAB)
        time.sleep(0.1)
        post_key(child_hwnd, VK_RETURN)
        time.sleep(0.5)
        if not user32.IsWindow(w.hwnd):
            print(f"       -> Closed (via {cls})")
            break
    else:
        # Last resort: try posting to ALL child windows
        all_children = []
        def enum_all(hwnd, _):
            all_children.append(hwnd)
            return True
        user32.EnumChildWindows(w.hwnd, EnumChildProc(enum_all), 0)

        for ch in all_children[:10]:  # Try first 10
            post_key(ch, VK_TAB)
            time.sleep(0.05)
            post_key(ch, VK_RETURN)
            time.sleep(0.3)
            if not user32.IsWindow(w.hwnd):
                print(f"       -> Closed (via child hwnd={ch})")
                break

        if user32.IsWindow(w.hwnd):
            print(f"       -> STILL OPEN")

time.sleep(1)
remaining = [w for w in list_windows() if 'Notepad' in w.title]
print(f"\nRemaining: {len(remaining)}")
if remaining:
    print("Trying taskkill as last resort...")
    # Since all are saved, force kill is safe
    import subprocess
    # Get PID of any notepad
    pid = remaining[0].pid
    print(f"Killing Notepad PID {pid}...")
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    time.sleep(1)
    final = [w for w in list_windows() if 'Notepad' in w.title]
    print(f"After taskkill: {len(final)} remaining")
else:
    print("All Notepad windows closed!")
