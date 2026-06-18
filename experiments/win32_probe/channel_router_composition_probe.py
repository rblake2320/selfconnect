"""Channel-router composition proof for SelfConnect.

This probe proves the policy shape that competitors must route around:

* terminal surfaces choose WM_CHAR;
* browser surfaces choose UIA Value/Invoke;
* routing metadata chooses a sidecar file/pipe-style control plane;
* governed mode requires a role lease;
* readback evidence is echo-filtered.

Default mode is a deterministic model proof that runs without a live desktop.
Use --live to additionally compose the existing throwaway terminal and local
browser probes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sc_echo_filter import EchoClassification, build_record  # noqa: E402
from sc_mesh_lease import GOVERNED_PROFILE, RoleLeaseTable, evaluate_lease_gate  # noqa: E402

TERMINAL_CLASSES = (
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "ConsoleWindowClass",
    "PseudoConsoleWindow",
    "mintty",
)

BROWSER_CLASSES = (
    "Chrome_WidgetWin_1",
    "ApplicationFrameWindow",
)


class ProofVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "NA"


class SurfaceKind(str, Enum):
    TERMINAL = "terminal"
    BROWSER = "browser"
    METADATA = "metadata"
    DESKTOP = "desktop"
    UNKNOWN = "unknown"


class WriteChannel(str, Enum):
    WM_CHAR = "wm_char"
    UIA_VALUE_INVOKE = "uia_value_invoke"
    FILE_REGISTRY = "file_registry"
    UIA_OR_WINDOW_MESSAGE = "uia_or_window_message"
    DENY = "deny"


class ReadChannel(str, Enum):
    UIA_TEXT_OR_CAPTURE = "uia_text_or_capture"
    REGISTRY_READ = "registry_read"
    NONE = "none"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class TargetFacts:
    """Redactable target identity facts used by the router."""

    hwnd: int
    pid: int
    exe_name: str
    class_name: str
    title: str
    role: str
    birth_id: str
    generation: int
    surface_hint: str = ""

    @property
    def target_hash(self) -> str:
        raw = "|".join(
            [
                str(self.hwnd),
                str(self.pid),
                self.exe_name,
                self.class_name,
                self.title,
                self.role,
                self.birth_id,
                str(self.generation),
            ]
        )
        return sha256_text(raw)


@dataclass
class ExpectedTarget:
    expected_pid: int = 0
    expected_exe: str = ""
    expected_class: str = ""
    expected_title_substr: str = ""
    allow_classes: tuple[str, ...] = ()
    require_terminal: bool = False
    require_browser: bool = False


@dataclass
class GuardDecision:
    ok: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class RouteDecision:
    action: str
    allowed: bool
    surface_kind: SurfaceKind
    write_channel: WriteChannel
    read_channel: ReadChannel
    profile: str
    role: str
    birth_id: str
    generation: int
    target_hash: str
    guard_ok: bool
    lease_ok: bool
    reason: str
    no_visible_metadata: bool = False
    mcp_required: bool = False


@dataclass
class ReadbackEvidence:
    target: str
    read_channel: ReadChannel
    sent_hash: str
    observed_hash: str
    classification: str
    method: str
    latency_ms: float


@dataclass
class ChannelRouterProof:
    verdict: ProofVerdict
    mode: str
    timestamp: float
    mcp_touched: bool
    raw_text_included: bool
    routes: list[RouteDecision] = field(default_factory=list)
    denial_checks: list[RouteDecision] = field(default_factory=list)
    readbacks: list[ReadbackEvidence] = field(default_factory=list)
    live_results: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def classify_surface(target: TargetFacts) -> SurfaceKind:
    hint = target.surface_hint.strip().lower()
    if hint == SurfaceKind.METADATA.value:
        return SurfaceKind.METADATA
    if target.class_name in TERMINAL_CLASSES:
        return SurfaceKind.TERMINAL
    if target.class_name in BROWSER_CLASSES:
        return SurfaceKind.BROWSER
    if target.class_name:
        return SurfaceKind.DESKTOP
    return SurfaceKind.UNKNOWN


def guard_target(target: TargetFacts, expected: ExpectedTarget) -> GuardDecision:
    reasons: list[str] = []

    if classify_surface(target) != SurfaceKind.METADATA:
        if target.hwnd <= 0:
            reasons.append("hwnd is not positive")
        if target.pid <= 0:
            reasons.append("pid is not positive")

    if expected.expected_pid and int(expected.expected_pid) != int(target.pid):
        reasons.append("pid mismatch")
    if expected.expected_exe:
        if expected.expected_exe.lower() != target.exe_name.lower():
            reasons.append("exe mismatch")
    if expected.expected_class and expected.expected_class != target.class_name:
        reasons.append("class mismatch")
    if expected.expected_title_substr:
        if expected.expected_title_substr.lower() not in target.title.lower():
            reasons.append("title mismatch")

    if expected.allow_classes and target.class_name not in expected.allow_classes:
        reasons.append("class not in allow list")
    if expected.require_terminal and target.class_name not in TERMINAL_CLASSES:
        reasons.append("target is not a terminal")
    if expected.require_browser and target.class_name not in BROWSER_CLASSES:
        reasons.append("target is not a browser")

    return GuardDecision(ok=not reasons, reasons=reasons)


def choose_write_channel(surface: SurfaceKind) -> WriteChannel:
    if surface == SurfaceKind.TERMINAL:
        return WriteChannel.WM_CHAR
    if surface == SurfaceKind.BROWSER:
        return WriteChannel.UIA_VALUE_INVOKE
    if surface == SurfaceKind.METADATA:
        return WriteChannel.FILE_REGISTRY
    if surface == SurfaceKind.DESKTOP:
        return WriteChannel.UIA_OR_WINDOW_MESSAGE
    return WriteChannel.DENY


def choose_read_channel(surface: SurfaceKind) -> ReadChannel:
    if surface in {SurfaceKind.TERMINAL, SurfaceKind.BROWSER, SurfaceKind.DESKTOP}:
        return ReadChannel.UIA_TEXT_OR_CAPTURE
    if surface == SurfaceKind.METADATA:
        return ReadChannel.REGISTRY_READ
    return ReadChannel.NONE


def plan_route(
    target: TargetFacts,
    *,
    action: str,
    expected: ExpectedTarget,
    profile: str,
    lease_table: RoleLeaseTable | None,
    owner_sid: str,
    now: float,
) -> RouteDecision:
    surface = classify_surface(target)
    guard = guard_target(target, expected)

    lease = evaluate_lease_gate(
        profile=profile,
        table=lease_table,
        mesh="default",
        role=target.role,
        generation=target.generation,
        hwnd=target.hwnd,
        owner_sid=owner_sid,
        birth_id=target.birth_id,
        now=now,
    )

    write_channel = choose_write_channel(surface)
    read_channel = choose_read_channel(surface)
    allowed = guard.ok and lease.ok and write_channel != WriteChannel.DENY
    if not guard.ok:
        reason = "; ".join(guard.reasons)
    elif not lease.ok:
        reason = lease.reason
    elif write_channel == WriteChannel.DENY:
        reason = "no route for surface"
    else:
        reason = "route allowed"

    return RouteDecision(
        action=action,
        allowed=allowed,
        surface_kind=surface,
        write_channel=write_channel if allowed else WriteChannel.DENY,
        read_channel=read_channel,
        profile=profile,
        role=target.role,
        birth_id=target.birth_id,
        generation=target.generation,
        target_hash=target.target_hash,
        guard_ok=guard.ok,
        lease_ok=lease.ok,
        reason=reason,
        no_visible_metadata=surface == SurfaceKind.METADATA,
        mcp_required=False,
    )


def _issue_for_target(
    table: RoleLeaseTable,
    target: TargetFacts,
    owner_sid: str,
    *,
    now: float,
) -> TargetFacts:
    lease = table.issue(
        mesh="default",
        role=target.role,
        hwnd=target.hwnd,
        pid=target.pid,
        exe_name=target.exe_name,
        class_name=target.class_name,
        title=target.title,
        owner_sid=owner_sid,
        ttl_s=300,
        now=now,
        birth_id=target.birth_id,
    )
    return replace(target, generation=lease.generation)


def _model_targets() -> tuple[TargetFacts, TargetFacts, TargetFacts]:
    terminal = TargetFacts(
        hwnd=1001,
        pid=21001,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="SC_ROUTER_TERMINAL_TARGET",
        role="terminal-peer",
        birth_id="b-terminal-001",
        generation=0,
    )
    browser = TargetFacts(
        hwnd=1002,
        pid=21002,
        exe_name="msedge.exe",
        class_name="Chrome_WidgetWin_1",
        title="SC_ROUTER_BROWSER_TARGET",
        role="browser-peer",
        birth_id="b-browser-001",
        generation=0,
    )
    metadata = TargetFacts(
        hwnd=1003,
        pid=21003,
        exe_name="selfconnect-daemon.exe",
        class_name="SelfConnectControlPlane",
        title="SC_ROUTER_METADATA_TARGET",
        role="mesh-control",
        birth_id="b-control-001",
        generation=0,
        surface_hint="metadata",
    )
    return terminal, browser, metadata


def run_model_proof() -> ChannelRouterProof:
    """Run a deterministic proof of the router composition."""
    owner_sid = "S-1-5-21-selfconnect-proof"
    now = 1000.0
    table = RoleLeaseTable()
    terminal, browser, metadata = _model_targets()
    terminal = _issue_for_target(table, terminal, owner_sid, now=now)
    browser = _issue_for_target(table, browser, owner_sid, now=now + 1)
    metadata = _issue_for_target(table, metadata, owner_sid, now=now + 2)

    routes = [
        plan_route(
            terminal,
            action="peer_text_send",
            expected=ExpectedTarget(
                expected_pid=terminal.pid,
                expected_class=terminal.class_name,
                expected_title_substr="ROUTER_TERMINAL",
                allow_classes=TERMINAL_CLASSES,
                require_terminal=True,
            ),
            profile=GOVERNED_PROFILE,
            lease_table=table,
            owner_sid=owner_sid,
            now=now + 3,
        ),
        plan_route(
            browser,
            action="browser_body_fill_and_send",
            expected=ExpectedTarget(
                expected_pid=browser.pid,
                expected_class=browser.class_name,
                expected_title_substr="ROUTER_BROWSER",
                allow_classes=BROWSER_CLASSES,
                require_browser=True,
            ),
            profile=GOVERNED_PROFILE,
            lease_table=table,
            owner_sid=owner_sid,
            now=now + 4,
        ),
        plan_route(
            metadata,
            action="mesh_route_update",
            expected=ExpectedTarget(
                expected_pid=metadata.pid,
                expected_class=metadata.class_name,
                expected_title_substr="ROUTER_METADATA",
            ),
            profile=GOVERNED_PROFILE,
            lease_table=table,
            owner_sid=owner_sid,
            now=now + 5,
        ),
    ]

    stale_terminal = replace(terminal, generation=terminal.generation + 10)
    wrong_browser = replace(browser, class_name="Notepad", title="fake Copy button")
    denial_checks = [
        plan_route(
            stale_terminal,
            action="stale_terminal_send",
            expected=ExpectedTarget(
                expected_pid=terminal.pid,
                expected_class=terminal.class_name,
                expected_title_substr="ROUTER_TERMINAL",
                allow_classes=TERMINAL_CLASSES,
                require_terminal=True,
            ),
            profile=GOVERNED_PROFILE,
            lease_table=table,
            owner_sid=owner_sid,
            now=now + 6,
        ),
        plan_route(
            wrong_browser,
            action="wrong_browser_surface",
            expected=ExpectedTarget(
                expected_pid=browser.pid,
                expected_class=browser.class_name,
                expected_title_substr="ROUTER_BROWSER",
                allow_classes=BROWSER_CLASSES,
                require_browser=True,
            ),
            profile=GOVERNED_PROFILE,
            lease_table=table,
            owner_sid=owner_sid,
            now=now + 7,
        ),
    ]

    nonce = "SC_ROUTER_NONCE"
    delta = f"{nonce}\r\npeer response"
    rb = build_record(
        delta=delta,
        nonce=nonce,
        sent_text=f"{nonce}\r",
        hwnd=terminal.hwnd,
        pid=terminal.pid,
        readback_method="TextChanged_event",
        timestamp_send=now,
        timestamp_recv=now + 0.25,
    )
    readbacks = [
        ReadbackEvidence(
            target="terminal-peer",
            read_channel=ReadChannel.UIA_TEXT_OR_CAPTURE,
            sent_hash=rb.sent_hash,
            observed_hash=rb.observed_hash,
            classification=rb.classification.value,
            method=rb.readback_method,
            latency_ms=rb.latency_ms,
        ),
        ReadbackEvidence(
            target="browser-peer",
            read_channel=ReadChannel.UIA_TEXT_OR_CAPTURE,
            sent_hash=sha256_text("browser payload"),
            observed_hash=sha256_text("submitted sentinel observed"),
            classification=EchoClassification.EXTERNAL_OUTPUT.value,
            method="UIA_ValuePattern_InvokePattern_readback",
            latency_ms=400.0,
        ),
    ]

    checks = [
        routes[0].write_channel == WriteChannel.WM_CHAR,
        routes[1].write_channel == WriteChannel.UIA_VALUE_INVOKE,
        routes[2].write_channel == WriteChannel.FILE_REGISTRY,
        all(route.allowed for route in routes),
        all(not route.allowed for route in denial_checks),
        routes[2].no_visible_metadata,
        all(not route.mcp_required for route in routes),
        readbacks[0].classification == EchoClassification.MIXED.value,
    ]
    verdict = ProofVerdict.PASS if all(checks) else ProofVerdict.FAIL
    return ChannelRouterProof(
        verdict=verdict,
        mode="model",
        timestamp=time.time(),
        mcp_touched=False,
        raw_text_included=False,
        routes=routes,
        denial_checks=denial_checks,
        readbacks=readbacks,
        notes=[
            "One governed policy model selected different native channels.",
            "Terminal route used WM_CHAR.",
            "Browser route used UIA Value/Invoke.",
            "Metadata route stayed off visible terminal text.",
            "Stale generation and wrong target facts failed closed.",
            "Echo-filtered readback produced a mixed echo/external result.",
        ],
    )


def _redact_route(route: RouteDecision) -> dict[str, Any]:
    data = asdict(route)
    data["surface_kind"] = route.surface_kind.value
    data["write_channel"] = route.write_channel.value
    data["read_channel"] = route.read_channel.value
    # The route already omits raw title/exe/class. Keep only a hash.
    return data


def sanitize_record(record: ChannelRouterProof) -> dict[str, Any]:
    return {
        "verdict": record.verdict.value,
        "mode": record.mode,
        "timestamp": record.timestamp,
        "mcp_touched": record.mcp_touched,
        "raw_text_included": record.raw_text_included,
        "routes": [_redact_route(route) for route in record.routes],
        "denial_checks": [_redact_route(route) for route in record.denial_checks],
        "readbacks": [
            {
                "target": item.target,
                "read_channel": item.read_channel.value,
                "sent_hash": item.sent_hash,
                "observed_hash": item.observed_hash,
                "classification": item.classification,
                "method": item.method,
                "latency_ms": item.latency_ms,
            }
            for item in record.readbacks
        ],
        "live_results": record.live_results,
        "notes": record.notes,
        "redacted": True,
    }


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def run_live_probe(timeout_s: float = 25.0) -> ChannelRouterProof:
    """Run model proof plus existing live throwaway terminal/browser probes."""
    proof = run_model_proof()
    proof.mode = "live"

    if sys.platform != "win32":
        proof.verdict = ProofVerdict.NA
        proof.live_results = {"na_reason": "Win32 platform required for live probes"}
        return proof

    root = _repo_root()
    sys.path.insert(0, str(root / "experiments" / "win32_probe"))

    try:
        import browser_local_proof  # type: ignore[import]
        import uia_echo_filter_probe  # type: ignore[import]
    except Exception as exc:
        proof.verdict = ProofVerdict.NA
        proof.live_results = {"na_reason": f"live probe imports failed: {exc}"}
        return proof

    terminal = uia_echo_filter_probe.run_probe(timeout_s=min(timeout_s, 15.0))
    browser = browser_local_proof.run_probe(timeout_s=timeout_s)
    terminal_ok = getattr(terminal, "result", "") == uia_echo_filter_probe.ProbeResult.PASS
    browser_ok = getattr(browser, "verdict", "") == browser_local_proof.BrowserProofVerdict.PASS
    browser_input = str(getattr(browser, "input_method", ""))
    browser_uia_ok = browser_input.startswith("uia")

    proof.live_results = {
        "terminal": {
            "verdict": getattr(getattr(terminal, "result", ""), "value", str(getattr(terminal, "result", ""))),
            "method": getattr(terminal, "uia_method", ""),
            "event_supported": bool(getattr(terminal, "event_supported", False)),
            "latency_ms": float(getattr(terminal, "latency_ms", 0.0)),
        },
        "browser": {
            "verdict": getattr(getattr(browser, "verdict", ""), "value", str(getattr(browser, "verdict", ""))),
            "input_method": browser_input,
            "read_method": getattr(browser, "read_method", ""),
            "form_submitted": bool(getattr(browser, "form_submitted", False)),
            "protected_detected": bool(getattr(browser, "protected_detected", False)),
        },
    }

    if proof.verdict != ProofVerdict.PASS:
        return proof
    if terminal_ok and browser_ok and browser_uia_ok:
        proof.verdict = ProofVerdict.PASS
        proof.notes.append("Live throwaway terminal and browser probes passed.")
    elif terminal_ok and browser_ok:
        proof.verdict = ProofVerdict.NA
        proof.notes.append(
            "Live probes passed but browser used a non-UIA fallback; keep model proof only."
        )
    else:
        proof.verdict = ProofVerdict.NA
        proof.notes.append("One or more live probes were unavailable or failed.")
    return proof


def write_artifact(record: ChannelRouterProof, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sanitize_record(record), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SelfConnect channel-router proof")
    parser.add_argument("--live", action="store_true", help="compose live throwaway probes")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--output", default="", help="write redacted JSON proof artifact")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    record = run_live_probe(args.timeout) if args.live else run_model_proof()
    if args.output:
        write_artifact(record, args.output)
    print(f"[CHANNEL_ROUTER] result={record.verdict.value} mode={record.mode}")
    if args.verbose or record.verdict != ProofVerdict.PASS:
        print(json.dumps(sanitize_record(record), indent=2, sort_keys=True))
    return 0 if record.verdict == ProofVerdict.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
