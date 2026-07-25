"""Hash-linked execution evidence."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from sc_tasks import FileLock


class EvidenceStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **details: Any) -> dict[str, Any]:
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with FileLock(lock):
            previous = ""
            if self.path.exists():
                lines = self.path.read_text(encoding="utf-8").splitlines()
                if lines:
                    previous = str(json.loads(lines[-1]).get("event_hash", ""))
            record = {
                "version": 1,
                "event_id": uuid.uuid4().hex,
                "created_at": time.time(),
                "event": event,
                "previous_hash": previous,
                "details": self._safe(details),
            }
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            record["event_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
            return record

    @staticmethod
    def _safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): (
                    "[redacted]"
                    if str(key).casefold() in {
                        "password", "token", "secret", "api_key", "authorization", "content",
                    }
                    else EvidenceStore._safe(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [EvidenceStore._safe(item) for item in value[:100]]
        if isinstance(value, str):
            return value[:4_000]
        return value

    def verify(self) -> dict[str, Any]:
        previous = ""
        count = 0
        if not self.path.exists():
            return {"ok": True, "records": 0, "head_hash": ""}
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            record = json.loads(line)
            event_hash = record.pop("event_hash", "")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if event_hash != expected or record.get("previous_hash") != previous:
                return {"ok": False, "records": count, "line": line_number}
            previous = event_hash
            count += 1
        return {"ok": True, "records": count, "head_hash": previous}
