#!/usr/bin/env python3
"""
SelfConnect — Telegram Approval Bridge

When Claude Code pauses for a tool-use approval, this script:
  1. Detects the approval prompt in the Claude terminal (via UIA text extraction)
  2. Sends a formatted message to your Telegram chat
  3. Waits for your reply ("yes" / "no")
  4. Injects the answer into the terminal via PostMessage(WM_CHAR)
  5. Confirms the action back to you on Telegram

Setup:
    1. Copy .env.approval.example -> .env.approval and fill in values
    2. pip install python-telegram-bot python-dotenv
    3. python approval_telegram.py
"""

import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ─── Config ──────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent / ".env.approval")

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID_RAW: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Only accept commands from this Telegram user ID.
# Get yours: message @userinfobot on Telegram.
# REQUIRED — if unset, the bridge refuses all incoming messages.
ALLOWED_USER_ID: int = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0"))

# How often to scan the Claude terminal (seconds)
POLL_INTERVAL: float = float(os.getenv("APPROVAL_POLL_INTERVAL", "2.0"))

# Titles / exe names of windows to monitor
CLAUDE_TITLE_HINTS: tuple[str, ...] = ("claude", "cmd", "powershell", "windows terminal")

# ─── Approval prompt patterns ─────────────────────────────────────────────────

APPROVAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"Do you want to proceed", re.IGNORECASE),
    re.compile(r"Allow.*for this project", re.IGNORECASE),
    re.compile(r">\s*Yes\s*No", re.IGNORECASE),
    re.compile(r"\bYes\b.*\bNo\b.*\bAlways allow\b", re.IGNORECASE | re.DOTALL),
]

# Pull the tool name from the approval prompt text
TOOL_NAME_RE: re.Pattern = re.compile(
    r'Bash\([^)]*\)|Edit\([^)]*\)|Write\([^)]*\)|Read\([^)]*\)|'
    r'Glob\([^)]*\)|Grep\([^)]*\)|WebFetch\([^)]*\)|WebSearch\([^)]*\)',
    re.IGNORECASE,
)

# Responses we accept as "yes"
YES_TOKENS: frozenset[str] = frozenset({"yes", "y", "approve", "ok", "go", "✅"})
# Responses we accept as "no"
NO_TOKENS: frozenset[str] = frozenset({"no", "n", "deny", "reject", "stop", "❌"})

# ─── Runtime state ────────────────────────────────────────────────────────────

# Set once the app is initialised
_app: Application | None = None
_loop: asyncio.AbstractEventLoop | None = None

# When we're waiting for Telegram reply, store the hwnd here.
# None means we are not currently waiting for an approval.
_pending: dict = {}  # keys: hwnd (int), values: {"tool": str, "sent_at": float}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _chat_id() -> int:
    raw = CHAT_ID_RAW.strip()
    if not raw or raw == "REPLACE_WITH_CHAT_ID":
        raise ValueError("TELEGRAM_CHAT_ID not configured in .env.approval")
    return int(raw)


def _find_claude_window():
    """Return the first Claude Code terminal window, or None."""
    try:
        from self_connect import list_windows
    except ImportError:
        print("[approval] ERROR: selfconnect not installed — pip install -e .")
        return None

    for w in list_windows():
        title_lower = (w.title or "").lower()
        exe_lower = (w.exe_name or "").lower()
        if any(hint in title_lower or hint in exe_lower for hint in CLAUDE_TITLE_HINTS):
            return w
    return None


def _has_approval_prompt(hwnd: int) -> bool:
    """Return True if the window currently shows a Claude Code approval prompt."""
    try:
        from self_connect import get_text_uia
        text = get_text_uia(hwnd) or ""
    except Exception:
        return False
    return any(p.search(text) for p in APPROVAL_PATTERNS)


def _extract_tool_name(hwnd: int) -> str:
    """Pull the tool name from the terminal text, or return '(unknown tool)'."""
    try:
        from self_connect import get_text_uia
        text = get_text_uia(hwnd) or ""
    except Exception:
        return "(unknown tool)"
    m = TOOL_NAME_RE.search(text)
    return m.group(0) if m else "(unknown tool)"


def _inject(hwnd: int, answer: str) -> None:
    """Send 'y\r' or 'n\r' into the terminal."""
    from self_connect import list_windows, send_string
    for w in list_windows():
        if w.hwnd == hwnd:
            send_string(w, f"{answer}\r")
            return
    print(f"[approval] WARNING: hwnd {hwnd} gone before injection")


async def _send_approval_request(win) -> None:
    """Send the approval prompt to Telegram and record the pending state."""
    global _app, _pending
    if _app is None:
        return

    hwnd = win.hwnd
    tool = _extract_tool_name(hwnd)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    msg = (
        "⚠️ Claude Code needs approval\n\n"
        f"Tool: {tool}\n"
        f"Window: {win.title[:60] if win.title else '(no title)'}\n"
        f"Time: {now}\n\n"
        "Reply:\n"
        "  ✅ yes — approve\n"
        "  ❌ no  — deny"
    )

    try:
        chat_id = _chat_id()
        await _app.bot.send_message(chat_id=chat_id, text=msg)
        print(f"[approval] -> Telegram: waiting for approval of {tool}")
        _pending[hwnd] = {"tool": tool, "sent_at": time.time()}
    except Exception as exc:
        print(f"[approval] Failed to send Telegram message: {exc}")


async def _monitor_loop() -> None:
    """Periodically scan for new Claude Code approval prompts."""
    print(f"[approval] Monitor started — scanning every {POLL_INTERVAL}s")
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            win = _find_claude_window()
            if win is None:
                continue

            hwnd = win.hwnd

            # Skip if we already have a pending approval for this window
            if hwnd in _pending:
                # Check for timeout — if >5 min, clear stale entry
                if time.time() - _pending[hwnd]["sent_at"] > 300:
                    print(f"[approval] Pending for hwnd {hwnd} timed out (5 min) — clearing")
                    del _pending[hwnd]
                continue

            if _has_approval_prompt(hwnd):
                await _send_approval_request(win)

        except Exception as exc:
            print(f"[approval] Monitor error: {exc}")


# ─── Telegram handler ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a Telegram reply and inject it into the terminal."""
    msg = update.message
    if not msg or not msg.text:
        return

    # ── Allowlist check ───────────────────────────────────────────────────────
    if ALLOWED_USER_ID == 0:
        print("[approval] WARNING: TELEGRAM_ALLOWED_USER_ID not set — ignoring all messages")
        return
    if msg.from_user is None or msg.from_user.id != ALLOWED_USER_ID:
        print(f"[approval] Rejected message from user_id={getattr(msg.from_user, 'id', '?')}")
        return

    # ── Chat ID check ─────────────────────────────────────────────────────────
    try:
        expected_chat_id = _chat_id()
    except ValueError:
        return
    if msg.chat.id != expected_chat_id:
        return

    raw = msg.text.strip().lower()
    if raw in YES_TOKENS:
        decision = "y"
        label = "Approved"
        icon = "✅"
    elif raw in NO_TOKENS:
        decision = "n"
        label = "Denied"
        icon = "❌"
    else:
        # Not a recognised command — ignore silently (could be unrelated chat)
        print(f"[approval] Unrecognised reply: {msg.text!r} — ignored")
        return

    if not _pending:
        await msg.reply_text("No pending approval right now.")
        return

    # Apply to the oldest pending approval
    hwnd = min(_pending, key=lambda h: _pending[h]["sent_at"])
    entry = _pending.pop(hwnd)
    tool = entry["tool"]

    _inject(hwnd, decision)
    print(f"[approval] {label}: {tool} (hwnd={hwnd})")

    confirm = f"{icon} {label} — Claude is continuing\nTool: {tool}"
    try:
        await msg.reply_text(confirm)
    except Exception as exc:
        print(f"[approval] Failed to send confirmation: {exc}")


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main() -> None:
    global _app, _loop

    if not BOT_TOKEN or BOT_TOKEN == "REPLACE_WITH_BOT_TOKEN":
        print("[approval] ERROR: TELEGRAM_BOT_TOKEN not set in .env.approval — exiting")
        return

    try:
        chat_id = _chat_id()
    except ValueError as exc:
        print(f"[approval] ERROR: {exc} — exiting")
        return

    if ALLOWED_USER_ID == 0:
        print("[approval] ERROR: TELEGRAM_ALLOWED_USER_ID not set — exiting")
        print("           Get your ID: message @userinfobot on Telegram")
        return

    print("[approval] SelfConnect — Telegram Approval Bridge")
    print(f"[approval] Chat ID       : {chat_id}")
    print(f"[approval] Allowed user  : {ALLOWED_USER_ID}")
    print(f"[approval] Poll interval : {POLL_INTERVAL}s")
    print(f"[approval] Watching for  : {', '.join(CLAUDE_TITLE_HINTS)}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    _app = app
    _loop = asyncio.get_running_loop()

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        print("[approval] Telegram polling active — bridge is live")
        print("[approval] Reply 'yes' or 'no' to approve/deny prompts")

        # Keep a strong reference so the task isn't GC'd (RUF006)
        _tasks: set = set()
        _tasks.add(asyncio.create_task(_monitor_loop()))

        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            print("[approval] Shutting down...")

        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    print("[approval] Stopped")


if __name__ == "__main__":
    asyncio.run(main())
