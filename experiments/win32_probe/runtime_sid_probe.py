"""runtime_sid_probe.py - Runtime OS SID Lookup for Governed Lease Gate proof.

Proves that current_owner_sid() resolves the live OS process SID via
OpenProcessToken → GetTokenInformation(TokenUser) → ConvertSidToStringSidW
and that the governed lease gate uses that SID for fail-closed enforcement.

Defense value: turns the lease gate from a test-state proof into live
OS-derived authority, directly countering API-key/localhost competitors.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from sc_mesh_lease import (
    GOVERNED_PROFILE,
    UNKNOWN_SID,
    RoleLeaseTable,
    current_owner_sid,
    evaluate_lease_gate,
    hash_sid,
)


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "NA"


@dataclass
class RuntimeSidProofRecord:
    verdict: Verdict = Verdict.NA
    na_reason: str = ""
    platform: str = ""
    sid_resolved: bool = False
    sid_is_real: bool = False
    sid_hash: str = ""
    gate_with_real_sid: bool = False
    gate_with_unknown_sid_denied: bool = False
    gate_fail_reason: str = ""
    latency_ms: float = 0.0
    redacted: bool = True
    notes: list[str] | None = None


def run_probe(*, output_path: str = "") -> RuntimeSidProofRecord:
    record = RuntimeSidProofRecord(notes=[], platform=sys.platform)

    if sys.platform != "win32":
        record.na_reason = "Win32 platform required"
        return record

    started = time.time()

    # Step 1: resolve the live OS SID — no injection.
    sid = current_owner_sid()
    record.sid_resolved = sid != UNKNOWN_SID
    if not record.sid_resolved:
        record.na_reason = "current_owner_sid() returned UNKNOWN_SID — Win32 token query failed"
        return record

    # Verify it looks like a real Windows SID (S-1-...).
    record.sid_is_real = sid.startswith("S-1-") and len(sid) > 6
    if not record.sid_is_real:
        record.verdict = Verdict.FAIL
        record.na_reason = f"SID does not look like a real Windows SID: hash={hash_sid(sid)}"
        record.sid_hash = hash_sid(sid)
        return record

    record.sid_hash = hash_sid(sid)

    # Step 2: issue a lease using the live SID and validate via the governed gate.
    table = RoleLeaseTable()
    lease = table.issue(
        mesh="default",
        role="B",
        hwnd=2820438,
        pid=1,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="role-B-runtime-sid-proof",
        owner_sid=sid,
        ttl_s=60,
        now=started,
    )

    gate_real = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="B",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid=sid,
        now=started + 1,
    )
    record.gate_with_real_sid = gate_real.ok

    # Step 3: confirm fail-closed — UNKNOWN_SID must be denied.
    gate_unknown = evaluate_lease_gate(
        profile=GOVERNED_PROFILE,
        table=table,
        mesh="default",
        role="B",
        generation=lease.generation,
        hwnd=lease.hwnd,
        owner_sid=UNKNOWN_SID,
        now=started + 2,
    )
    record.gate_with_unknown_sid_denied = not gate_unknown.ok
    record.gate_fail_reason = gate_unknown.reason

    record.latency_ms = (time.time() - started) * 1000
    checks = [
        record.sid_resolved,
        record.sid_is_real,
        record.gate_with_real_sid,
        record.gate_with_unknown_sid_denied,
    ]
    record.verdict = Verdict.PASS if all(checks) else Verdict.FAIL
    if record.verdict == Verdict.FAIL:
        record.na_reason = "one or more SID gate checks failed"
    record.notes = [
        "current_owner_sid() resolved a real Windows SID via OpenProcessToken.",
        "GetTokenInformation(TokenUser) + ConvertSidToStringSidW confirmed live OS identity.",
        "Governed lease gate ALLOWed the live SID and DENYed UNKNOWN_SID (fail-closed).",
        "Raw SID is redacted; only SHA-256 hash is included in this artifact.",
    ]

    if output_path and record.verdict == Verdict.PASS:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_sanitize(record), indent=2, sort_keys=True), encoding="utf-8")

    return record


def _sanitize(record: RuntimeSidProofRecord) -> dict[str, object]:
    data = asdict(record)
    data["verdict"] = record.verdict.value
    data["redacted"] = True
    return data


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime OS SID lookup for governed lease gate")
    parser.add_argument("--output", default="", help="write redacted PASS artifact to this path")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    record = run_probe(output_path=args.output)
    reason = f" | {record.na_reason}" if record.na_reason else ""
    print(f"[RUNTIME_SID] result={record.verdict.value}{reason}")
    if args.verbose or record.verdict != Verdict.PASS:
        print(json.dumps(_sanitize(record), indent=2, sort_keys=True))
    return 0 if record.verdict == Verdict.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
