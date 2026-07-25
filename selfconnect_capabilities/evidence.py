"""Authenticated, hash-linked execution evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any

from sc_tasks import FileLock

from .integrity import IntegrityKey, canonical_bytes


class EvidenceStore:
    SENSITIVE_KEYS = {
        "password", "token", "secret", "api_key", "authorization", "content",
    }
    SENSITIVE_FLAGS = {
        "--password", "--token", "--secret", "--api-key", "--authorization",
        "-p",
    }

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.witness_path = path.with_suffix(path.suffix + ".witness")
        self.integrity = IntegrityKey(path.parent)

    def append(self, event: str, **details: Any) -> dict[str, Any]:
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with FileLock(lock):
            verification = self._verify_unlocked()
            if not verification["ok"]:
                raise ValueError("execution evidence chain verification failed before append")
            sequence = int(verification["records"]) + 1
            record = {
                "version": 2,
                "sequence": sequence,
                "event_id": uuid.uuid4().hex,
                "created_at": time.time(),
                "event": event,
                "previous_hash": str(verification["head_hash"]),
                "details": self._safe(details),
            }
            record["event_hash"] = self.integrity.digest(record)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_witness(sequence, record["event_hash"])
            return record

    @staticmethod
    def _safe(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                name = str(key)
                folded = name.casefold()
                if folded in EvidenceStore.SENSITIVE_KEYS:
                    result[name] = "[redacted]"
                elif folded == "argv" and isinstance(item, list):
                    result[name] = EvidenceStore._safe_argv(item)
                else:
                    result[name] = EvidenceStore._safe(item)
            return result
        if isinstance(value, list):
            return [EvidenceStore._safe(item) for item in value[:100]]
        if isinstance(value, str):
            return "[redacted]" if EvidenceStore._looks_secret(value) else value[:4_000]
        return value

    @staticmethod
    def _safe_argv(value: list[Any]) -> list[Any]:
        result: list[Any] = []
        redact_next = False
        for item in value[:100]:
            text = str(item)
            folded = text.casefold()
            if redact_next:
                result.append("[redacted]")
                redact_next = False
                continue
            matching_flag = next(
                (
                    flag
                    for flag in EvidenceStore.SENSITIVE_FLAGS
                    if folded == flag or folded.startswith(flag + "=")
                ),
                None,
            )
            if matching_flag:
                if "=" in text:
                    result.append(text.split("=", 1)[0] + "=[redacted]")
                else:
                    result.append(text)
                    redact_next = True
                continue
            result.append(EvidenceStore._safe(text))
        return result

    @staticmethod
    def _looks_secret(value: str) -> bool:
        candidate = value.strip()
        if len(candidate) < 24 or " " in candidate:
            return False
        counts = {char: candidate.count(char) for char in set(candidate)}
        entropy = -sum(
            (count / len(candidate)) * math.log2(count / len(candidate))
            for count in counts.values()
        )
        return entropy >= 4.25

    def verify(self) -> dict[str, Any]:
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with FileLock(lock):
            return self._verify_unlocked()

    def _verify_unlocked(self) -> dict[str, Any]:
        previous = ""
        count = 0
        if not self.path.exists():
            return self._verify_witness({"ok": True, "records": 0, "head_hash": ""})
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            record = json.loads(line)
            event_hash = str(record.pop("event_hash", ""))
            if int(record.get("version", 1)) >= 2:
                expected = self.integrity.digest(record)
                sequence_ok = record.get("sequence") == count + 1
            else:
                expected = hashlib.sha256(canonical_bytes(record)).hexdigest()
                sequence_ok = True
            if (
                not hmac.compare_digest(event_hash, expected)
                or record.get("previous_hash") != previous
                or not sequence_ok
            ):
                return {"ok": False, "records": count, "line": line_number}
            previous = event_hash
            count += 1
        return self._verify_witness({"ok": True, "records": count, "head_hash": previous})

    def _write_witness(self, count: int, head_hash: str) -> None:
        payload = {"version": 1, "records": count, "head_hash": head_hash}
        payload["witness_hmac"] = self.integrity.digest(payload)
        temp = self.witness_path.with_suffix(f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.witness_path)

    def _verify_witness(self, result: dict[str, Any]) -> dict[str, Any]:
        if not self.witness_path.exists():
            return result
        witness = json.loads(self.witness_path.read_text(encoding="utf-8"))
        actual_hmac = str(witness.pop("witness_hmac", ""))
        if not hmac.compare_digest(actual_hmac, self.integrity.digest(witness)):
            return {
                "ok": False,
                "records": result["records"],
                "reason": "witness_authentication_failed",
            }
        if (
            witness.get("records") != result["records"]
            or witness.get("head_hash") != result["head_hash"]
        ):
            return {
                "ok": False,
                "records": result["records"],
                "reason": "tail_truncation_or_rollback",
            }
        return result

    def records(self) -> list[dict[str, Any]]:
        verification = self.verify()
        if not verification["ok"]:
            raise ValueError("execution evidence chain verification failed")
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def get(self, event_id: str) -> dict[str, Any] | None:
        return next(
            (record for record in reversed(self.records()) if record.get("event_id") == event_id),
            None,
        )

    def find(self, event: str, **details: Any) -> dict[str, Any] | None:
        for record in reversed(self.records()):
            if record.get("event") != event:
                continue
            values = record.get("details", {})
            if all(values.get(key) == value for key, value in details.items()):
                return record
        return None
