"""Fresh, source-attributed machine state for capability planning."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sc_tasks import FileLock


@dataclass(frozen=True)
class Observation:
    key: str
    value: Any
    source: str
    confidence: float
    observed_at: float
    expires_at: float
    sensitive: bool = False
    value_digest: str = ""
    untrusted_data: bool = True

    def __post_init__(self) -> None:
        if not self.key or not self.source:
            raise ValueError("world-state key and source are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be later than observed_at")

    def is_fresh(self, now: float | None = None) -> bool:
        return self.expires_at > (time.time() if now is None else now)

    def public_dict(self, now: float | None = None) -> dict[str, Any]:
        result = asdict(self)
        result["fresh"] = self.is_fresh(now)
        return result


class WorldStateStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "world_state.json"
        self.changes_path = root / "world_changes.jsonl"
        root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def observe(
        self,
        key: str,
        value: Any,
        *,
        source: str,
        confidence: float = 1.0,
        ttl_seconds: float = 60.0,
        sensitive: bool = False,
        now: float | None = None,
    ) -> Observation:
        observed_at = time.time() if now is None else now
        digest = self._digest(value)
        stored_value = "[sensitive]" if sensitive else value
        observation = Observation(
            key=key,
            value=stored_value,
            source=source,
            confidence=float(confidence),
            observed_at=observed_at,
            expires_at=observed_at + max(0.1, float(ttl_seconds)),
            sensitive=bool(sensitive),
            value_digest=digest,
            untrusted_data=True,
        )
        lock = self.path.with_suffix(".lock")
        with FileLock(lock):
            values = self._read_unlocked()
            previous = values.get(key)
            values[key] = observation.public_dict(observed_at)
            self._write_unlocked(values)
            change = {
                "version": 1,
                "changed_at": observed_at,
                "key": key,
                "source": source,
                "value_digest": digest,
                "previous_digest": str((previous or {}).get("value_digest", "")),
                "sensitive": bool(sensitive),
            }
            with self.changes_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(change, sort_keys=True, ensure_ascii=True) + "\n")
        return observation

    def get(self, key: str, *, include_stale: bool = False, now: float | None = None) -> dict[str, Any] | None:
        value = self._read().get(key)
        if value is None:
            return None
        value["fresh"] = float(value["expires_at"]) > (time.time() if now is None else now)
        return value if include_stale or value["fresh"] else None

    def snapshot(
        self,
        *,
        prefix: str = "",
        include_stale: bool = False,
        now: float | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        current = time.time() if now is None else now
        observations = []
        for key, value in sorted(self._read().items()):
            if prefix and not key.startswith(prefix):
                continue
            value["fresh"] = float(value["expires_at"]) > current
            if value["fresh"] or include_stale:
                observations.append(value)
            if len(observations) >= max(1, min(limit, 500)):
                break
        return {
            "ok": True,
            "observed_at": current,
            "prefix": prefix,
            "observations": observations,
            "fresh": sum(1 for item in observations if item["fresh"]),
            "stale": sum(1 for item in observations if not item["fresh"]),
        }

    def changes(self, *, since: float = 0.0, limit: int = 100) -> dict[str, Any]:
        rows = []
        if self.changes_path.exists():
            for line in self.changes_path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if float(item.get("changed_at", 0)) > since:
                    rows.append(item)
        return {"ok": True, "changes": rows[-max(1, min(limit, 500)):]}

    def _read(self) -> dict[str, dict[str, Any]]:
        lock = self.path.with_suffix(".lock")
        with FileLock(lock):
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def _write_unlocked(self, value: dict[str, Any]) -> None:
        temp = self.path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8")
        os.replace(temp, self.path)
