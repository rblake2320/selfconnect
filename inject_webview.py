"""
SelfConnect: Electron/WebView2 Chat Injection via UIA + WM_CHAR
===============================================================
Works for any Electron app with a chat input panel:
  - Antigravity (standalone Google IDE, Gemini 3.1 Pro / Claude 4.6)
  - VS Code extensions: GitHub Copilot, Continue, Amazon Q, Gemini Code Assist
  - Cursor, Windsurf, and other Electron-based AI editors

Technique:
1. AccessibleObjectFromWindow → triggers Chromium's UIA accessibility bridge
2. UIA set_focus() on the chat input element
3. PostMessage(WM_CHAR) for each character → bypasses OSR input routing
4. UIA invoke() on the Send button

No clipboard, no SendInput, no foreground window required.
"""
import sys, ctypes, time, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ctypes.windll.shcore.SetProcessDpiAwareness(2)

user32 = ctypes.windll.user32
WM_CHAR = 0x0102

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)


def find_vscode_windows():
    """Return list of (hwnd, title, pid) for all VS Code windows."""
    found = []
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        cls   = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if cls.value == 'Chrome_WidgetWin_1' and title.value:
            found.append((hwnd, title.value, pid.value))
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def find_render_widget(parent_hwnd):
    renders = []
    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if 'renderwidget' in cls.value.lower():
            renders.append(hwnd)
        return True
    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(cb), 0)
    return renders


def trigger_accessibility(chrome_hwnd):
    """Trigger VS Code's UIA accessibility bridge (expands tree from 2 → 268+ nodes)."""
    import comtypes
    oleacc = ctypes.windll.oleacc
    IID = comtypes.GUID("{618736e0-3c3d-11cf-810c-00aa00389b71}")
    acc = ctypes.c_void_p()
    oleacc.AccessibleObjectFromWindow(
        ctypes.c_void_p(chrome_hwnd), ctypes.c_ulong(0xFFFFFFFC),
        ctypes.byref(IID), ctypes.byref(acc)
    )
    time.sleep(1.2)


def inject_message(target_hwnd, message, input_name="Message input", send_btn_name="Send message"):
    """
    Inject message into WebView2 chat input and submit.

    Args:
        target_hwnd: HWND of the VS Code / Electron main window
        message: Text to send
        input_name: UIA name of the chat input Edit control
        send_btn_name: UIA name of the Send button
    """
    renders = find_render_widget(target_hwnd)
    if not renders:
        print("ERROR: No Chrome_RenderWidgetHostHWND found")
        return False
    chrome_hwnd = renders[0]
    print(f"Render widget: 0x{chrome_hwnd:x}")

    trigger_accessibility(chrome_hwnd)

    from pywinauto import Application
    app = Application(backend='uia').connect(handle=target_hwnd)
    dlg = app.window(handle=target_hwnd)
    all_desc = dlg.descendants()

    # Dismiss any notification popups
    for elem in all_desc:
        try:
            if (elem.element_info.control_type == 'Button' and
                    'dismiss' in (elem.element_info.name or '').lower()):
                print(f"Dismissing: {elem.element_info.name}")
                elem.invoke()
                time.sleep(0.3)
        except Exception:
            pass

    # Find chat input
    msg_input = None
    for elem in all_desc:
        try:
            n  = elem.element_info.name or ''
            ct = elem.element_info.control_type or ''
            if ct == 'Edit' and input_name.lower() in n.lower():
                msg_input = elem
                break
        except Exception:
            pass

    if not msg_input:
        print(f"ERROR: Could not find input '{input_name}'")
        return False
    print(f"Chat input: {msg_input.rectangle()}")

    # Set focus via UIA (bypasses foreground requirement)
    msg_input.set_focus()
    time.sleep(0.3)

    # Type via WM_CHAR to Chrome render widget (bypasses OSR)
    print(f"Typing {len(message)} chars...")
    for ch in message:
        user32.PostMessageW(chrome_hwnd, WM_CHAR, ord(ch), 1)
        time.sleep(0.012)
    time.sleep(0.4)

    # Find and invoke Send button
    # Re-scan after typing (tree may have updated)
    all_desc2 = dlg.descendants()
    send_btn = None
    for elem in all_desc2:
        try:
            n  = elem.element_info.name or ''
            ct = elem.element_info.control_type or ''
            if ct == 'Button' and n == send_btn_name:
                send_btn = elem
                break
        except Exception:
            pass

    if send_btn:
        print(f"Invoking Send: {send_btn.rectangle()}")
        send_btn.invoke()
        print("Message sent!")
        return True
    else:
        print(f"WARNING: Send button '{send_btn_name}' not found. Message is typed but not sent.")
        return False


def read_response(target_hwnd, wait_secs=15):
    """Poll UIA tree for new Text content in the Antigravity panel."""
    import comtypes
    oleacc = ctypes.windll.oleacc
    renders = find_render_widget(target_hwnd)
    if renders:
        IID = comtypes.GUID("{618736e0-3c3d-11cf-810c-00aa00389b71}")
        acc = ctypes.c_void_p()
        oleacc.AccessibleObjectFromWindow(ctypes.c_void_p(renders[0]), ctypes.c_ulong(0xFFFFFFFC),
                                          ctypes.byref(IID), ctypes.byref(acc))

    from pywinauto import Application

    class RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
    r = RECT()
    user32.GetWindowRect(target_hwnd, ctypes.byref(r))
    panel_x_min = r.left + (r.right - r.left) * 3 // 4  # right quarter of window

    app = Application(backend='uia').connect(handle=target_hwnd)
    dlg = app.window(handle=target_hwnd)

    for i in range(wait_secs // 2):
        time.sleep(2)
        all_desc = dlg.descendants()
        texts = []
        for elem in all_desc:
            try:
                n  = elem.element_info.name or ''
                ct = elem.element_info.control_type or ''
                rect = elem.rectangle()
                if ct == 'Text' and rect.left > panel_x_min and len(n) > 5:
                    if not any(kw in n for kw in ['Minimize', 'Maximize', 'Close', 'Gemini', 'AM', 'PM']):
                        texts.append(n)
            except Exception:
                pass
        if texts:
            print(f"\nResponse text ({len(texts)} segments):")
            for t in texts[:20]:
                print(f"  {t[:100]}")
            return texts
    return []


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Inject message into VS Code WebView chat')
    parser.add_argument('--hwnd', type=lambda x: int(x, 16), default=0xca088e,
                        help='Target window HWND (hex, default=0xca088e)')
    parser.add_argument('--message', default='Hello from Claude Agent-A. What model are you?')
    parser.add_argument('--list', action='store_true', help='List VS Code windows and exit')
    args = parser.parse_args()

    if args.list:
        print("VS Code / Electron windows:")
        for hwnd, title, pid in find_vscode_windows():
            print(f"  0x{hwnd:x}  pid={pid}  '{title[:80]}'")
        sys.exit(0)

    print(f"Target: 0x{args.hwnd:x}")
    print(f"Message: {args.message}")
    ok = inject_message(args.hwnd, args.message)
    if ok:
        print("\nWaiting for response...")
        texts = read_response(args.hwnd, wait_secs=20)
        if not texts:
            print("No response text detected in time window.")
