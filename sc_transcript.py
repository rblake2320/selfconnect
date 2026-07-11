"""sc_transcript — read spawned agents' session transcripts instead of screens.

Every Claude Code session writes a structured JSONL transcript under
``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``. Tailing that file is
lossless and structured, unlike PrintWindow/OCR/console scraping. This module
locates the session file for a spawned agent (newest file in the project dir
created after spawn time) and extracts assistant messages from it.

No Win32 — pure file I/O, works from any node with access to the profile dir.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

__version__ = "0.12.0"

_ENCODE_RE = re.compile(r"[\\/:. ]")


def projects_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"


def encode_project_dir(cwd: str | Path) -> str:
    """Encode a working directory the way Claude Code names project dirs.

    ``C:\\Users\\techai`` -> ``C--Users-techai`` (path separators, colon,
    dots and spaces all become ``-``).
    """
    return _ENCODE_RE.sub("-", str(cwd)).rstrip("-")


def project_dir_for(cwd: str | Path) -> Path:
    return projects_root() / encode_project_dir(cwd)


def find_session_files(cwd: str | Path, since_ts: float = 0.0) -> list[Path]:
    """Session transcripts for ``cwd`` modified after ``since_ts``, newest first."""
    proj = project_dir_for(cwd)
    if not proj.is_dir():
        return []
    files = [p for p in proj.glob("*.jsonl") if p.stat().st_mtime >= since_ts]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def newest_session(cwd: str | Path, since_ts: float = 0.0) -> Optional[Path]:
    files = find_session_files(cwd, since_ts)
    return files[0] if files else None


def wait_for_session(cwd: str | Path, since_ts: float, timeout: float = 60.0,
                     poll: float = 0.5) -> Optional[Path]:
    """Wait for a spawned agent's transcript to appear (created after spawn)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = newest_session(cwd, since_ts)
        if found is not None:
            return found
        time.sleep(poll)
    return None


def _entry_text(entry: dict) -> str:
    """Flatten an assistant transcript entry's content blocks to plain text."""
    message = entry.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p)


def read_entries(transcript: str | Path, offset: int = 0) -> tuple[list[dict], int]:
    """Read JSONL entries starting at byte ``offset``; returns (entries, new_offset).

    Incremental tailing: pass the returned offset back on the next call.
    Incomplete trailing lines (a write in flight) are left for the next read.
    """
    path = Path(transcript)
    entries: list[dict] = []
    with path.open("rb") as f:
        f.seek(offset)
        buf = f.read()
    consumed = 0
    for line in buf.split(b"\n"):
        # only count lines that are newline-terminated within buf
        end = consumed + len(line) + 1
        if end > len(buf):
            break  # trailing partial line — not terminated yet
        consumed = end
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries, offset + consumed


def assistant_messages(transcript: str | Path) -> list[str]:
    """All assistant message texts in the transcript, in order."""
    entries, _ = read_entries(transcript)
    out = []
    for e in entries:
        if e.get("type") == "assistant":
            text = _entry_text(e)
            if text:
                out.append(text)
    return out


def last_assistant_message(transcript: str | Path) -> str:
    msgs = assistant_messages(transcript)
    return msgs[-1] if msgs else ""


def wait_for_assistant_reply(transcript: str | Path, after_count: int = -1,
                             timeout: float = 300.0, poll: float = 1.0) -> str:
    """Block until a NEW assistant message appears beyond ``after_count``.

    Pass ``after_count=len(assistant_messages(t))`` before injecting, then
    call this — it returns the first reply produced after your injection.
    ``after_count=-1`` means "count whatever is there now first".
    """
    if after_count < 0:
        try:
            after_count = len(assistant_messages(transcript))
        except OSError:
            after_count = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msgs = assistant_messages(transcript)
        except OSError:
            msgs = []
        if len(msgs) > after_count:
            return msgs[after_count]
        time.sleep(poll)
    return ""


__all__ = [
    "assistant_messages",
    "encode_project_dir",
    "find_session_files",
    "last_assistant_message",
    "newest_session",
    "project_dir_for",
    "projects_root",
    "read_entries",
    "wait_for_assistant_reply",
    "wait_for_session",
]
