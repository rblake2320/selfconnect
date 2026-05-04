"""
SelfConnect: Antigravity Controller — High-Level Automation SDK
===============================================================
Provides programmatic control over Antigravity (Google's standalone Electron IDE)
via Win32 UIA + WM_CHAR injection. Zero API keys required.

Glossary:
    HWND  — Handle to a WiNDow. The unique integer identifier that Windows assigns
            to every visible window. Used by all Win32 API calls to target a specific
            window (e.g. PostMessage, GetWindowRect, IsWindow).
    UIA   — UI Automation. Microsoft's accessibility API for reading and controlling
            UI elements programmatically (find controls, set focus, invoke buttons).
    OSR   — Offscreen Rendering. Chromium/WebView2 mode that renders the page into
            a bitmap rather than a real Win32 window — which blocks external SendInput
            from reaching the web content. WM_CHAR PostMessage bypasses this.
    WM_CHAR — Windows Message: Character. Delivers a Unicode character directly to a
              window's message queue, bypassing OSR input routing.

Technique (proven in inject_webview.py, session 12):
1. AccessibleObjectFromWindow → triggers Chromium UIA accessibility bridge
2. UIA set_focus() on chat input → bypasses foreground requirement
3. PostMessage(WM_CHAR) per character → bypasses OSR input routing
4. UIA invoke() on Send button → submits without click events

Usage:
    from antigravity_controller import connect, chat

    session = connect()
    response = chat(session, "Hello, what model are you?")
    print(response)

CLI:
    python antigravity_controller.py --list
    python antigravity_controller.py --chat "What model are you?"
    python antigravity_controller.py --buttons
    python antigravity_controller.py --model
"""

import ctypes
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ctypes.windll.shcore.SetProcessDpiAwareness(2)

user32 = ctypes.windll.user32
WM_CHAR = 0x0102
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

__all__ = [
    "AntigravitySession",
    "connect",
    "send_message",
    "read_latest_response",
    "chat",
    "list_buttons",
    "click_button",
    "get_model",
    "set_model",
    "new_chat",
    "AntigravityMonitor",
]

_VALID_EVENTS = {"response", "error", "model_changed"}


# ─── Dataclass ────────────────────────────────────────────────────────────────


@dataclass
class AntigravitySession:
    """
    Represents a live connection to an Antigravity (or Electron chat) window.

    Attributes:
        hwnd        — Handle to a WiNDow (HWND): the integer ID Windows uses to
                      identify the main Antigravity application window. Passed to
                      all Win32 API calls that target this window.
        chrome_hwnd — HWND of the Chrome_RenderWidgetHostHWND child window inside
                      the Electron shell. WM_CHAR messages are sent here to reach
                      the WebView renderer, bypassing OSR input blocking.
    """

    hwnd: int                          # Main Chrome_WidgetWin_1 HWND
    chrome_hwnd: int                   # Chrome_RenderWidgetHostHWND child
    pid: int
    title: str
    model: str = ""                    # e.g. "Gemini 3.1 Pro (High)"
    is_standalone: bool = True         # True = Antigravity app, False = VS Code ext
    uia_ready: bool = False
    connected_at: float = field(default_factory=time.time)

    def is_valid(self) -> bool:
        """Return True if the HWND is still a live window."""
        return bool(user32.IsWindow(self.hwnd))

    def __str__(self) -> str:
        status = "valid" if self.is_valid() else "GONE"
        return (
            f"AntigravitySession(hwnd=0x{self.hwnd:x}, pid={self.pid}, "
            f"model='{self.model}', standalone={self.is_standalone}, "
            f"uia_ready={self.uia_ready}, status={status})"
        )


# ─── Private Helpers ──────────────────────────────────────────────────────────


def _find_chrome_windows() -> list[tuple[int, str, int]]:
    """Return list of (hwnd, title, pid) for all Chrome_WidgetWin_1 windows."""
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if cls.value == "Chrome_WidgetWin_1" and title.value:
            found.append((hwnd, title.value, pid.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def _find_render_widget(parent_hwnd: int) -> list[int]:
    """Return all Chrome_RenderWidgetHostHWND child handles."""
    renders = []

    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if "renderwidget" in cls.value.lower():
            renders.append(hwnd)
        return True

    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(cb), 0)
    return renders


def _trigger_accessibility(chrome_hwnd: int) -> bool:
    """
    Trigger Chromium's UIA accessibility bridge via AccessibleObjectFromWindow.
    Expands UIA tree from 2 nodes → 268+ accessible elements including WebView content.
    Returns True on success.
    """
    try:
        import comtypes
        oleacc = ctypes.windll.oleacc
        IID = comtypes.GUID("{618736e0-3c3d-11cf-810c-00aa00389b71}")
        acc = ctypes.c_void_p()
        oleacc.AccessibleObjectFromWindow(
            ctypes.c_void_p(chrome_hwnd),
            ctypes.c_ulong(0xFFFFFFFC),  # OBJID_CLIENT
            ctypes.byref(IID),
            ctypes.byref(acc),
        )
        time.sleep(1.2)
        return True
    except Exception as exc:
        print(f"WARNING: UIA accessibility trigger failed: {exc}")
        return False


def _is_antigravity_title(title: str) -> bool:
    """
    Return True if the window title looks like Antigravity (not Google Chrome browser,
    not VS Code, not Cursor, etc.).
    """
    t = title.lower()
    return (
        "antigravity" in t
        and "google chrome" not in t
        and "visual studio code" not in t
    )


def _get_uia_descendants(hwnd: int):
    """Connect pywinauto to hwnd and return a fresh descendant list."""
    from pywinauto import Application
    app = Application(backend="uia").connect(handle=hwnd)
    dlg = app.window(handle=hwnd)
    return dlg, dlg.descendants()


def _find_element(hwnd: int, control_type: str, name_contains: str = "",
                  name_exact: str = ""):
    """
    Walk the UIA descendant tree for hwnd and return the first element
    matching control_type and name filter. Returns None if not found.
    name_contains: case-insensitive substring match
    name_exact: exact match (takes priority over name_contains)
    """
    try:
        _, descendants = _get_uia_descendants(hwnd)
        for elem in descendants:
            try:
                n = elem.element_info.name or ""
                ct = elem.element_info.control_type or ""
                if ct != control_type:
                    continue
                if name_exact and n == name_exact:
                    return elem
                if name_contains and name_contains.lower() in n.lower():
                    return elem
            except Exception:
                pass
    except Exception:
        pass
    return None


def _find_elements(hwnd: int, control_type: str,
                   name_contains: str = "") -> list:
    """
    Walk UIA descendant tree and return all elements matching control_type
    and optional name_contains filter.
    """
    results = []
    try:
        _, descendants = _get_uia_descendants(hwnd)
        for elem in descendants:
            try:
                n = elem.element_info.name or ""
                ct = elem.element_info.control_type or ""
                if ct != control_type:
                    continue
                if name_contains and name_contains.lower() not in n.lower():
                    continue
                results.append(elem)
            except Exception:
                pass
    except Exception:
        pass
    return results


def _dismiss_notifications(hwnd: int) -> None:
    """Invoke any Dismiss/Close popup buttons to clear the UIA tree."""
    try:
        _, descendants = _get_uia_descendants(hwnd)
        for elem in descendants:
            try:
                if elem.element_info.control_type == "Button":
                    name = (elem.element_info.name or "").lower()
                    if "dismiss" in name or "close notification" in name:
                        elem.invoke()
                        time.sleep(0.3)
            except Exception:
                pass
    except Exception:
        pass


def _snapshot_text_elements(hwnd: int) -> set[str]:
    """Return the set of all current Text element names (for baseline delta)."""
    texts = set()
    try:
        _, descendants = _get_uia_descendants(hwnd)
        for elem in descendants:
            try:
                if elem.element_info.control_type == "Text":
                    n = elem.element_info.name or ""
                    if n:
                        texts.add(n)
            except Exception:
                pass
    except Exception:
        pass
    return texts


def _has_stop_button(hwnd: int) -> bool:
    """Return True if a Stop/Cancel generation button is present in the UIA tree."""
    try:
        _, descendants = _get_uia_descendants(hwnd)
        for elem in descendants:
            try:
                n = (elem.element_info.name or "").lower()
                ct = elem.element_info.control_type or ""
                if ct == "Button" and ("stop" in n or "cancel" in n):
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _read_model_from_button(hwnd: int) -> str:
    """Extract current model name from the 'Select model' button text."""
    try:
        _, descendants = _get_uia_descendants(hwnd)
        for elem in descendants:
            try:
                n = elem.element_info.name or ""
                ct = elem.element_info.control_type or ""
                if ct == "Button" and "select model" in n.lower():
                    # Name format: "Select model, current: Gemini 3.1 Pro (High)"
                    if "current:" in n.lower():
                        return n.split("current:")[-1].strip().rstrip(")")
                    return n
            except Exception:
                pass
    except Exception:
        pass
    return ""


# ─── Public Functions ─────────────────────────────────────────────────────────


def connect(hwnd: int = 0) -> "AntigravitySession":
    """
    Connect to a running Antigravity window.

    Args:
        hwnd: Specific HWND to target. If 0, auto-discovers Antigravity windows.

    Returns:
        AntigravitySession populated with hwnd, chrome_hwnd, pid, title, model.

    Raises:
        ImportError: if comtypes or pywinauto are not installed.
        RuntimeError: if no Antigravity window found, no render widget, or UIA fails.
    """
    try:
        import comtypes  # noqa: F401
        from pywinauto import Application  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            f"Missing dependency: {exc}\n"
            "Install with: pip install comtypes pywinauto"
        ) from exc

    if hwnd:
        windows = _find_chrome_windows()
        match = [(h, t, p) for h, t, p in windows if h == hwnd]
        if not match:
            raise RuntimeError(f"HWND 0x{hwnd:x} not found or not a Chrome_WidgetWin_1 window")
        hwnd, title, pid = match[0]
        is_standalone = _is_antigravity_title(title)
    else:
        windows = _find_chrome_windows()
        candidates = [(h, t, p) for h, t, p in windows if _is_antigravity_title(t)]
        if not candidates:
            raise RuntimeError(
                "No Antigravity window found. Is the app running?\n"
                f"Found Chrome windows: {[t for _, t, _ in windows]}"
            )
        hwnd, title, pid = candidates[0]
        is_standalone = True

    renders = _find_render_widget(hwnd)
    if not renders:
        raise RuntimeError(
            f"No Chrome_RenderWidgetHostHWND found inside 0x{hwnd:x}. "
            "Is Antigravity fully loaded?"
        )
    chrome_hwnd = renders[0]

    uia_ok = _trigger_accessibility(chrome_hwnd)
    if not uia_ok:
        raise RuntimeError("UIA accessibility bridge failed to initialize.")

    model = _read_model_from_button(hwnd)

    return AntigravitySession(
        hwnd=hwnd,
        chrome_hwnd=chrome_hwnd,
        pid=pid,
        title=title,
        model=model,
        is_standalone=is_standalone,
        uia_ready=True,
    )


def send_message(session: AntigravitySession, text: str,
                 input_name: str = "Message input",
                 send_btn_name: str = "Send message") -> bool:
    """
    Type `text` into the Antigravity chat input and submit.

    Uses UIA set_focus + WM_CHAR PostMessage (bypasses foreground requirement
    and WebView2 OSR input blocking).

    Returns True if message was sent, False if input or send button not found.
    """
    if not session.is_valid():
        print("ERROR: Session HWND is no longer valid.")
        return False

    # Re-trigger accessibility bridge to ensure tree is fresh
    _trigger_accessibility(session.chrome_hwnd)
    _dismiss_notifications(session.hwnd)

    # Find chat input
    msg_input = _find_element(session.hwnd, "Edit", name_contains=input_name)
    if not msg_input:
        print(f"ERROR: Could not find input '{input_name}'")
        return False

    print(f"Chat input found: {msg_input.rectangle()}")

    # UIA set_focus bypasses Win32 foreground requirement
    msg_input.set_focus()
    time.sleep(0.3)

    # PostMessage WM_CHAR — bypasses WebView2 OSR input routing
    print(f"Typing {len(text)} chars via WM_CHAR...")
    for ch in text:
        user32.PostMessageW(session.chrome_hwnd, WM_CHAR, ord(ch), 1)
        time.sleep(0.012)
    time.sleep(0.4)

    # Re-scan — UIA tree updates after text entry (Send button may appear/enable)
    send_btn = _find_element(session.hwnd, "Button", name_exact=send_btn_name)
    if not send_btn:
        # Try partial match as fallback
        send_btn = _find_element(session.hwnd, "Button", name_contains="send")

    if send_btn:
        print(f"Invoking Send button: {send_btn.rectangle()}")
        send_btn.invoke()
        print("Message sent.")
        return True

    print(f"WARNING: Send button '{send_btn_name}' not found. Message typed but not submitted.")
    return False


def read_latest_response(session: AntigravitySession, timeout: int = 30,
                         poll_interval: float = 1.0) -> str:
    """
    Wait for Antigravity/Gemini to finish generating a response and return the text.

    Detection strategy:
    1. Baseline: snapshot all current Text elements before polling
    2. Poll every poll_interval seconds:
       - If stop button present: generation is in progress
       - If stop button gone AND new text stable across 2 consecutive polls: done
    3. Raise TimeoutError if timeout exceeded

    Returns the new response text (delta from baseline), joined with newlines.
    """
    baseline = _snapshot_text_elements(session.hwnd)
    last_delta: set[str] = set()
    stable_count = 0
    ever_saw_stop = False
    elapsed = 0.0

    while elapsed < timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval

        stop_present = _has_stop_button(session.hwnd)
        if stop_present:
            ever_saw_stop = True
            stable_count = 0
            continue

        current = _snapshot_text_elements(session.hwnd)
        delta = current - baseline

        # Filter out UI chrome (timestamps, button labels, etc.)
        filtered = {t for t in delta if len(t) > 5 and not any(
            kw in t for kw in ["Minimize", "Maximize", "Close", "AM", "PM",
                               "Send message", "Message input", "Select model",
                               "New chat", "Settings"]
        )}

        if filtered and filtered == last_delta:
            stable_count += 1
            if stable_count >= 2 or (not ever_saw_stop and stable_count >= 3):
                # Response is stable
                return "\n".join(sorted(filtered, key=len))
        else:
            stable_count = 0
            last_delta = filtered

    raise TimeoutError(
        f"No stable response detected within {timeout}s. "
        "Check if Antigravity is responding."
    )


def chat(session: AntigravitySession, message: str, timeout: int = 30) -> str:
    """
    Send a message and return Gemini's response.

    Composes send_message() + read_latest_response().

    Raises:
        RuntimeError: if send_message fails
        TimeoutError: if no response within timeout seconds
    """
    ok = send_message(session, message)
    if not ok:
        raise RuntimeError(f"Failed to send message: '{message[:60]}...'")
    return read_latest_response(session, timeout=timeout)


def list_buttons(session: AntigravitySession) -> list[str]:
    """Return names of all Button elements currently in the Antigravity UIA tree."""
    buttons = _find_elements(session.hwnd, "Button")
    return [elem.element_info.name for elem in buttons if elem.element_info.name]


def click_button(session: AntigravitySession, name: str) -> bool:
    """
    Find a Button by exact name and invoke it.
    Returns True if found and invoked, False otherwise.
    """
    btn = _find_element(session.hwnd, "Button", name_exact=name)
    if btn:
        btn.invoke()
        return True
    return False


def get_model(session: AntigravitySession) -> str:
    """
    Read the current model from the 'Select model' button and update session.model.
    Returns the model name string (e.g. "Gemini 3.1 Pro (High)").
    """
    model = _read_model_from_button(session.hwnd)
    session.model = model
    return model


def set_model(session: AntigravitySession, model_name: str) -> bool:
    """
    Open the model selector and click the option matching model_name.
    Returns True if the model option was found and clicked.
    """
    # Open selector
    selector = _find_element(session.hwnd, "Button", name_contains="Select model")
    if not selector:
        print("ERROR: 'Select model' button not found.")
        return False
    selector.invoke()
    time.sleep(0.5)

    # Find model option in the expanded selector
    option = _find_element(session.hwnd, "ListItem", name_contains=model_name)
    if not option:
        option = _find_element(session.hwnd, "MenuItem", name_contains=model_name)
    if not option:
        option = _find_element(session.hwnd, "Button", name_contains=model_name)

    if option:
        option.invoke()
        time.sleep(0.3)
        session.model = get_model(session)
        return True

    print(f"ERROR: Model option '{model_name}' not found in selector.")
    return False


def new_chat(session: AntigravitySession) -> bool:
    """
    Click the 'New chat' button to start a fresh conversation.
    Returns True if found and clicked.
    """
    result = click_button(session, "New chat")
    if not result:
        # Try partial match
        btn = _find_element(session.hwnd, "Button", name_contains="new chat")
        if btn:
            btn.invoke()
            return True
    return result


# ─── AntigravityMonitor ───────────────────────────────────────────────────────


class AntigravityMonitor:
    """
    Background daemon that polls the Antigravity UIA tree and emits events.

    Events:
        'response'      — handler(response_text: str) — Gemini finished responding
        'error'         — handler(exc: Exception) — poll error (monitor continues)
        'model_changed' — handler(new_model: str) — model selector changed

    Usage:
        monitor = AntigravityMonitor(session, poll=1.5)
        monitor.on('response', lambda r: print(f"Got: {r}"))
        monitor.on('model_changed', lambda m: print(f"Model: {m}"))
        monitor.start()
        # ... do work ...
        monitor.stop()
    """

    def __init__(self, session: AntigravitySession, poll: float = 1.5):
        self._session = session
        self._poll = poll
        self._handlers: dict[str, list[Callable]] = {e: [] for e in _VALID_EVENTS}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def on(self, event: str, handler: Callable) -> "AntigravityMonitor":
        """Register an event handler. Returns self for chaining."""
        if event not in _VALID_EVENTS:
            raise ValueError(f"Unknown event '{event}'. Valid: {_VALID_EVENTS}")
        with self._lock:
            self._handlers[event].append(handler)
        return self

    def _emit(self, event: str, payload) -> None:
        with self._lock:
            handlers = list(self._handlers[event])
        for h in handlers:
            try:
                h(payload)
            except Exception as exc:
                print(f"[AntigravityMonitor] Handler error for '{event}': {exc}")

    def start(self) -> "AntigravityMonitor":
        """Start the background polling thread. Returns self for chaining."""
        if self._thread and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="AntigravityMonitor", daemon=True
        )
        self._thread.start()
        return self

    def stop(self, timeout: float | None = None) -> None:
        """Signal the monitor to stop and wait for the thread to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        """Return True if the background thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        """Background polling loop — runs in daemon thread."""
        # Initialize COM for this thread
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

        baseline = _snapshot_text_elements(self._session.hwnd)
        last_model = self._session.model
        last_delta: set[str] = set()
        stable_count = 0
        ever_saw_stop = False

        while not self._stop_event.is_set():
            try:
                if not self._session.is_valid():
                    self._emit("error", RuntimeError("Session HWND is no longer valid"))
                    break

                stop_present = _has_stop_button(self._session.hwnd)
                if stop_present:
                    ever_saw_stop = True
                    stable_count = 0
                    self._stop_event.wait(self._poll)
                    continue

                # Check for model change
                current_model = _read_model_from_button(self._session.hwnd)
                if current_model and current_model != last_model:
                    last_model = current_model
                    self._session.model = current_model
                    self._emit("model_changed", current_model)

                current = _snapshot_text_elements(self._session.hwnd)
                delta = current - baseline
                filtered = {t for t in delta if len(t) > 5 and not any(
                    kw in t for kw in ["Minimize", "Maximize", "Close", "AM", "PM",
                                       "Send message", "Message input", "Select model",
                                       "New chat", "Settings"]
                )}

                if filtered and filtered == last_delta:
                    stable_count += 1
                    if stable_count >= 2 or (not ever_saw_stop and stable_count >= 3):
                        response_text = "\n".join(sorted(filtered, key=len))
                        self._emit("response", response_text)
                        # Reset baseline to current so we detect the NEXT response
                        baseline = current
                        last_delta = set()
                        stable_count = 0
                        ever_saw_stop = False
                elif filtered != last_delta:
                    stable_count = 0
                    last_delta = filtered

            except Exception as exc:
                self._emit("error", exc)

            self._stop_event.wait(self._poll)

        try:
            import comtypes
            comtypes.CoUninitialize()
        except Exception:
            pass


# ─── CLI ──────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Antigravity Controller — SelfConnect SDK"
    )
    parser.add_argument(
        "--hwnd", type=lambda x: int(x, 16), default=0,
        help="Target HWND (hex). Auto-discovers if not set."
    )
    parser.add_argument("--list", action="store_true", help="List Antigravity windows and exit")
    parser.add_argument("--chat", metavar="MSG", help="Send a message and print the response")
    parser.add_argument("--buttons", action="store_true", help="List all button names in UIA tree")
    parser.add_argument("--model", action="store_true", help="Print the current model name")
    parser.add_argument(
        "--timeout", type=int, default=30,
        help="Response timeout in seconds (default: 30)"
    )
    args = parser.parse_args()

    if args.list:
        print("Scanning for Antigravity windows...")
        windows = _find_chrome_windows()
        anti = [(h, t, p) for h, t, p in windows if _is_antigravity_title(t)]
        if not anti:
            print("No Antigravity windows found.")
            print("All Chrome_WidgetWin_1 windows:")
            for h, t, p in windows:
                print(f"  0x{h:x}  pid={p}  '{t[:80]}'")
        else:
            print(f"Found {len(anti)} Antigravity window(s):")
            for h, t, p in anti:
                print(f"  0x{h:x}  pid={p}  '{t[:80]}'")
        sys.exit(0)

    print("Connecting to Antigravity...")
    try:
        session = connect(hwnd=args.hwnd)
    except (RuntimeError, ImportError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(session)

    if args.model:
        model = get_model(session)
        print(f"Current model: {model or '(unknown)'}")

    elif args.buttons:
        btns = list_buttons(session)
        print(f"Buttons ({len(btns)}):")
        for b in btns:
            print(f"  {b}")

    elif args.chat:
        print(f"Sending: {args.chat}")
        try:
            response = chat(session, args.chat, timeout=args.timeout)
            print("\n--- Response ---")
            print(response)
        except (RuntimeError, TimeoutError) as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    else:
        parser.print_help()
