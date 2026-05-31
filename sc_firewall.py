"""
sc_firewall.py — SelfConnect Runtime Decision Firewall  (v1.0.0)

Fills the gap identified in the 2026 agentic security analysis:
  "You've secured the channel, not the decision."

The OWASP Top 10 for Agentic Applications 2026 leads with:
  1. Agent Goal Hijack
  2. Tool Misuse
  3. Identity & Privilege Abuse   ← BPC/TSK covers this
  4. Memory Poisoning

BPC/TSK nail #3. This module covers #1, #2, and #4.

The root cause: LLMs cannot reliably distinguish instructions from data.
When an agent reads a web page, document, or email, it treats that content
the same as commands from its operator. A prompt-injected agent emits
perfectly BPC-paired, TSK-signed, ledger-logged malicious actions.

Simon Willison's "lethal trifecta":
  private-data access + untrusted-content exposure + external comms vector
  → any agent with all three is vulnerable, period.

SelfConnect's HID emulation is a maximal exfiltration/action vector.
This module puts a policy check between AXIOM's decision and SelfConnect's
HID emit (send_string, send_keys, click_at, etc.).

Architecture:

  InputProvenanceTagger
    Tags every input string as one of:
      INSTRUCTION  — from the operator/system prompt (trusted)
      DATA         — from a known-safe internal source (trusted for read)
      UNTRUSTED    — from web/file/email/external agent (never execute)
      UNKNOWN      — untagged (treated as UNTRUSTED by default)

  DecisionFirewall
    Policy gate between any agent decision and HID emission.
    Checks:
      1. Input provenance: blocks if the decision was triggered by UNTRUSTED input
         without an explicit operator override.
      2. Tool allowlist: blocks if the tool/action is not in the allowed set.
      3. Goal drift: blocks if the action diverges from the declared task goal
         (keyword-based heuristic; pluggable with an LLM judge).
      4. Exfiltration pattern: blocks if the payload matches known exfiltration
         patterns (base64 blobs, URLs, email addresses in HID output).
      5. Kill-switch: blocks everything if the global kill-switch is engaged.

  FirewallPolicy
    Declarative policy object — serialisable to/from JSON for audit.

References:
  - OWASP Agentic Applications Top 10 2026
  - Simon Willison "lethal trifecta" (2024)
  - OpenAI / DeepMind / Anthropic convergence on runtime guardrails
  - Straiker runtime decision firewall approach
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# InputProvenance
# ---------------------------------------------------------------------------

class InputProvenance(str, Enum):
    """Provenance classification for an input string."""
    INSTRUCTION = "INSTRUCTION"   # operator / system prompt — fully trusted
    DATA        = "DATA"          # known-safe internal source — trusted for read
    UNTRUSTED   = "UNTRUSTED"     # web / file / email / external agent
    UNKNOWN     = "UNKNOWN"       # untagged — treated as UNTRUSTED


@dataclass
class TaggedInput:
    """An input string with its provenance classification."""
    content: str
    provenance: InputProvenance
    source: str = ""          # human-readable source description
    tagged_at: float = field(default_factory=time.time)
    input_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def is_trusted(self) -> bool:
        return self.provenance in (InputProvenance.INSTRUCTION, InputProvenance.DATA)

    @property
    def is_untrusted(self) -> bool:
        return self.provenance in (InputProvenance.UNTRUSTED, InputProvenance.UNKNOWN)

    def to_dict(self) -> dict:
        return {
            "input_id": self.input_id,
            "provenance": self.provenance.value,
            "source": self.source,
            "tagged_at": self.tagged_at,
            "content_hash": hashlib.sha256(self.content.encode()).hexdigest()[:16],
        }


class InputProvenanceTagger:
    """
    Tags input strings with their provenance classification.

    The tagger maintains a registry of trusted sources and applies heuristic
    rules to classify unregistered inputs.

    Usage::

        tagger = InputProvenanceTagger()
        tagger.register_trusted_source("system_prompt", InputProvenance.INSTRUCTION)
        tagger.register_trusted_source("internal_db", InputProvenance.DATA)

        tagged = tagger.tag("ls -la", source="system_prompt")
        # → TaggedInput(provenance=INSTRUCTION)

        tagged2 = tagger.tag("<script>exfil(document.cookie)</script>", source="webpage")
        # → TaggedInput(provenance=UNTRUSTED)
    """

    # Heuristic patterns that strongly suggest untrusted/injected content
    _INJECTION_PATTERNS: list[re.Pattern] = [
        re.compile(r"ignore\s+(previous|all|prior)\s+instructions?", re.I),
        re.compile(r"you\s+are\s+now\s+(?:a|an)\s+\w+", re.I),
        re.compile(r"system\s*prompt\s*[:=]", re.I),
        re.compile(r"<\s*(?:script|iframe|object|embed)\b", re.I),
        re.compile(r"\bexfil(?:trate)?\b", re.I),
        re.compile(r"\bprompt\s+injection\b", re.I),
        re.compile(r"IGNORE\s+ABOVE", re.I),
        re.compile(r"NEW\s+INSTRUCTIONS?:", re.I),
        re.compile(r"OVERRIDE\s+(?:POLICY|RULES?|INSTRUCTIONS?)", re.I),
    ]

    def __init__(self) -> None:
        self._trusted_sources: dict[str, InputProvenance] = {}
        self._lock = threading.Lock()

    def register_trusted_source(
        self, source_name: str, provenance: InputProvenance
    ) -> None:
        """Register a named source as trusted at the given provenance level."""
        with self._lock:
            self._trusted_sources[source_name] = provenance

    def tag(
        self,
        content: str,
        source: str = "",
        override_provenance: Optional[InputProvenance] = None,
    ) -> TaggedInput:
        """
        Tag *content* with its provenance.

        Priority:
          1. override_provenance (explicit caller override)
          2. Registered trusted source name
          3. Injection pattern heuristics → UNTRUSTED
          4. Default → UNKNOWN
        """
        if override_provenance is not None:
            return TaggedInput(content, override_provenance, source)

        with self._lock:
            registered = self._trusted_sources.get(source)
        if registered is not None:
            return TaggedInput(content, registered, source)

        # Heuristic: check for injection patterns
        for pat in self._INJECTION_PATTERNS:
            if pat.search(content):
                return TaggedInput(content, InputProvenance.UNTRUSTED, source)

        # Unregistered source with no injection signal → UNKNOWN
        return TaggedInput(content, InputProvenance.UNKNOWN, source)

    def tag_batch(
        self, items: list[tuple[str, str]]
    ) -> list[TaggedInput]:
        """Tag a list of (content, source) pairs."""
        return [self.tag(content, source) for content, source in items]


# ---------------------------------------------------------------------------
# FirewallPolicy
# ---------------------------------------------------------------------------

@dataclass
class FirewallPolicy:
    """
    Declarative policy for the DecisionFirewall.

    Fields:
      allow_tools          — set of tool names allowed to emit HID actions
                             (empty set = allow all)
      block_on_untrusted   — block any action triggered by UNTRUSTED input
      block_exfiltration   — block actions matching exfiltration patterns
      goal_keywords        — if non-empty, block actions with no keyword overlap
                             with the declared task goal
      max_hid_rate         — max HID actions per second (0 = unlimited)
      audit_log_path       — path to write firewall decisions as JSONL
    """
    allow_tools: set[str] = field(default_factory=set)
    block_on_untrusted: bool = True
    block_exfiltration: bool = True
    goal_keywords: list[str] = field(default_factory=list)
    max_hid_rate: float = 0.0
    audit_log_path: str = "proofs/firewall_audit.jsonl"

    def to_dict(self) -> dict:
        return {
            "allow_tools": sorted(self.allow_tools),
            "block_on_untrusted": self.block_on_untrusted,
            "block_exfiltration": self.block_exfiltration,
            "goal_keywords": self.goal_keywords,
            "max_hid_rate": self.max_hid_rate,
            "audit_log_path": self.audit_log_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FirewallPolicy":
        return cls(
            allow_tools=set(d.get("allow_tools", [])),
            block_on_untrusted=d.get("block_on_untrusted", True),
            block_exfiltration=d.get("block_exfiltration", True),
            goal_keywords=d.get("goal_keywords", []),
            max_hid_rate=d.get("max_hid_rate", 0.0),
            audit_log_path=d.get("audit_log_path", "proofs/firewall_audit.jsonl"),
        )

    def save(self, path: str) -> None:
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "FirewallPolicy":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# FirewallDecision
# ---------------------------------------------------------------------------

class FirewallVerdict(str, Enum):
    ALLOW   = "ALLOW"
    BLOCK   = "BLOCK"
    ESCALATE = "ESCALATE"   # requires human approval before proceeding


@dataclass
class FirewallDecision:
    """Result of a firewall check."""
    verdict: FirewallVerdict
    reason: str
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)
    tool: str = ""
    payload_hash: str = ""
    input_provenance: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict == FirewallVerdict.ALLOW

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "ts": self.ts,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "tool": self.tool,
            "payload_hash": self.payload_hash,
            "input_provenance": self.input_provenance,
            "meta": self.meta,
        }


# ---------------------------------------------------------------------------
# DecisionFirewall
# ---------------------------------------------------------------------------

class DecisionFirewall:
    """
    Policy gate between any agent decision and HID emission.

    This is the runtime counterpart to WRAITH (dev-time red-teaming):
    WRAITH finds vulnerabilities at design time; DecisionFirewall enforces
    policy at execution time, every time.

    Usage::

        policy = FirewallPolicy(
            allow_tools={"send_string", "send_keys", "click_at"},
            block_on_untrusted=True,
            block_exfiltration=True,
            goal_keywords=["summarise", "report"],
        )
        firewall = DecisionFirewall(policy)

        # Before any HID action:
        decision = firewall.check(
            tool="send_string",
            payload="ls -la",
            triggering_inputs=[tagged_input],
        )
        if decision.allowed:
            send_string(target, "ls -la")

        # Wire into ApprovalRelay:
        firewall.on_block(lambda d: relay.queue_for_human(d))
    """

    # Exfiltration heuristics: patterns that suggest data exfiltration in HID output
    _EXFIL_PATTERNS: list[re.Pattern] = [
        # Long base64 blobs (>40 chars of base64 chars)
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
        # URLs with query strings (potential exfil endpoint)
        re.compile(r"https?://\S+\?\S+=\S+"),
        # Email addresses in output
        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        # curl / wget commands
        re.compile(r"\b(?:curl|wget)\s+", re.I),
        # AWS/GCP credential patterns
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    ]

    def __init__(
        self,
        policy: Optional[FirewallPolicy] = None,
        kill_switch: Optional["KillSwitch"] = None,  # type: ignore[name-defined]
    ) -> None:
        self.policy = policy or FirewallPolicy()
        self._kill_switch = kill_switch
        self._lock = threading.Lock()
        self._block_handlers: list[Callable[[FirewallDecision], None]] = []
        self._hid_timestamps: list[float] = []  # for rate limiting

    # ── registration ──────────────────────────────────────────────────────

    def on_block(self, handler: Callable[[FirewallDecision], None]) -> "DecisionFirewall":
        """Register a callback invoked when a decision is BLOCK or ESCALATE."""
        self._block_handlers.append(handler)
        return self

    # ── core check ────────────────────────────────────────────────────────

    def check(
        self,
        tool: str,
        payload: str = "",
        triggering_inputs: Optional[list[TaggedInput]] = None,
        meta: Optional[dict] = None,
    ) -> FirewallDecision:
        """
        Run all policy checks and return a FirewallDecision.

        Checks (in order, fail-fast):
          1. Kill-switch engaged → BLOCK
          2. Tool allowlist → BLOCK if not allowed
          3. Input provenance → BLOCK if any triggering input is UNTRUSTED
          4. Exfiltration pattern → BLOCK if payload matches
          5. Goal drift → ESCALATE if no keyword overlap with goal
          6. HID rate limit → BLOCK if rate exceeded
          → ALLOW
        """
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]
        worst_provenance = self._worst_provenance(triggering_inputs or [])

        def _decision(
            verdict: FirewallVerdict,
            reason: str,
        ) -> FirewallDecision:
            d = FirewallDecision(
                verdict=verdict,
                reason=reason,
                tool=tool,
                payload_hash=payload_hash,
                input_provenance=worst_provenance.value,
                meta=meta or {},
            )
            self._audit(d)
            if not d.allowed:
                for h in self._block_handlers:
                    try:
                        h(d)
                    except Exception:
                        pass
            return d

        # 1. Kill-switch
        if self._kill_switch and self._kill_switch.is_engaged:
            return _decision(FirewallVerdict.BLOCK, "kill-switch engaged")

        # 2. Tool allowlist
        if self.policy.allow_tools and tool not in self.policy.allow_tools:
            return _decision(
                FirewallVerdict.BLOCK,
                f"tool '{tool}' not in allowlist {sorted(self.policy.allow_tools)}",
            )

        # 3. Input provenance
        if self.policy.block_on_untrusted and worst_provenance in (
            InputProvenance.UNTRUSTED, InputProvenance.UNKNOWN
        ):
            return _decision(
                FirewallVerdict.BLOCK,
                f"triggering input has provenance={worst_provenance.value}; "
                "operator override required to proceed",
            )

        # 4. Exfiltration pattern
        if self.policy.block_exfiltration:
            for pat in self._EXFIL_PATTERNS:
                m = pat.search(payload)
                if m:
                    return _decision(
                        FirewallVerdict.BLOCK,
                        f"exfiltration pattern detected: {m.group()[:40]!r}",
                    )

        # 5. Goal drift
        if self.policy.goal_keywords:
            payload_lower = payload.lower()
            if not any(kw.lower() in payload_lower for kw in self.policy.goal_keywords):
                return _decision(
                    FirewallVerdict.ESCALATE,
                    "action has no keyword overlap with declared task goal — "
                    "requires human confirmation",
                )

        # 6. HID rate limit
        if self.policy.max_hid_rate > 0:
            now = time.time()
            with self._lock:
                self._hid_timestamps = [
                    t for t in self._hid_timestamps if now - t < 1.0
                ]
                if len(self._hid_timestamps) >= self.policy.max_hid_rate:
                    return _decision(
                        FirewallVerdict.BLOCK,
                        f"HID rate limit exceeded ({self.policy.max_hid_rate}/s)",
                    )
                self._hid_timestamps.append(now)

        return _decision(FirewallVerdict.ALLOW, "all checks passed")

    # ── convenience wrappers ──────────────────────────────────────────────

    def guard_send_string(
        self,
        target: Any,
        text: str,
        triggering_inputs: Optional[list[TaggedInput]] = None,
    ) -> FirewallDecision:
        """
        Check policy before calling send_string.
        Returns the decision — caller must check decision.allowed before
        calling the real send_string.
        """
        return self.check(
            tool="send_string",
            payload=text,
            triggering_inputs=triggering_inputs,
        )

    def guard_send_keys(
        self,
        keys: list[str],
        triggering_inputs: Optional[list[TaggedInput]] = None,
    ) -> FirewallDecision:
        return self.check(
            tool="send_keys",
            payload=" ".join(keys),
            triggering_inputs=triggering_inputs,
        )

    def guard_click(
        self,
        x: int,
        y: int,
        triggering_inputs: Optional[list[TaggedInput]] = None,
    ) -> FirewallDecision:
        return self.check(
            tool="click_at",
            payload=f"click({x},{y})",
            triggering_inputs=triggering_inputs,
        )

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _worst_provenance(inputs: list[TaggedInput]) -> InputProvenance:
        """Return the least-trusted provenance across all inputs."""
        order = [
            InputProvenance.UNTRUSTED,
            InputProvenance.UNKNOWN,
            InputProvenance.DATA,
            InputProvenance.INSTRUCTION,
        ]
        for prov in order:
            if any(i.provenance == prov for i in inputs):
                return prov
        return InputProvenance.INSTRUCTION  # no inputs → assume trusted

    def _audit(self, decision: FirewallDecision) -> None:
        import os
        try:
            os.makedirs(
                os.path.dirname(self.policy.audit_log_path) or ".", exist_ok=True
            )
            with open(self.policy.audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(decision.to_dict(), separators=(",", ":")) + "\n")
        except Exception:
            pass

    # ── policy management ─────────────────────────────────────────────────

    def update_policy(self, policy: FirewallPolicy) -> None:
        """Hot-swap the policy without restarting."""
        with self._lock:
            self.policy = policy

    def set_kill_switch(self, ks: "KillSwitch") -> None:  # type: ignore[name-defined]
        self._kill_switch = ks


# ---------------------------------------------------------------------------
# KillSwitch  (thread-safe halt + override for HID-level autonomy)
# ---------------------------------------------------------------------------

class KillSwitch:
    """
    Thread-safe halt and override mechanism for HID-level autonomy.

    With HID-level autonomy, "how a human halts or overrides a running agent
    mid-action" is safety-critical and an explicit DoD/Five Eyes expectation.

    The KillSwitch can be engaged from any thread (including a signal handler
    or a Telegram bot callback) and is checked by DecisionFirewall before
    every HID action.

    Usage::

        ks = KillSwitch()
        firewall = DecisionFirewall(policy, kill_switch=ks)

        # From any thread / signal handler:
        ks.engage(reason="operator halt")

        # Re-enable after human review:
        ks.release(operator_id="alice")

        # Register a callback for engagement events:
        ks.on_engage(lambda r: send_telegram_alert(r))
    """

    def __init__(self) -> None:
        self._engaged = threading.Event()
        self._reason: str = ""
        self._engaged_at: Optional[float] = None
        self._released_at: Optional[float] = None
        self._operator_id: str = ""
        self._lock = threading.Lock()
        self._engage_handlers: list[Callable[[str], None]] = []
        self._release_handlers: list[Callable[[str], None]] = []
        self._audit_log: list[dict] = []

    # ── engage / release ──────────────────────────────────────────────────

    def engage(self, reason: str = "operator halt") -> None:
        """
        Engage the kill-switch. All subsequent firewall checks will return BLOCK.
        Thread-safe; can be called from signal handlers.
        """
        with self._lock:
            self._engaged.set()
            self._reason = reason
            self._engaged_at = time.time()
            entry = {"event": "ENGAGE", "ts": self._engaged_at, "reason": reason}
            self._audit_log.append(entry)
        for h in self._engage_handlers:
            try:
                h(reason)
            except Exception:
                pass

    def release(self, operator_id: str = "") -> None:
        """
        Release the kill-switch after human review.
        Requires an operator_id for audit trail.
        """
        with self._lock:
            self._engaged.clear()
            self._released_at = time.time()
            self._operator_id = operator_id
            entry = {
                "event": "RELEASE",
                "ts": self._released_at,
                "operator_id": operator_id,
            }
            self._audit_log.append(entry)
        for h in self._release_handlers:
            try:
                h(operator_id)
            except Exception:
                pass

    @property
    def is_engaged(self) -> bool:
        return self._engaged.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    # ── callbacks ─────────────────────────────────────────────────────────

    def on_engage(self, handler: Callable[[str], None]) -> "KillSwitch":
        self._engage_handlers.append(handler)
        return self

    def on_release(self, handler: Callable[[str], None]) -> "KillSwitch":
        self._release_handlers.append(handler)
        return self

    # ── wait ──────────────────────────────────────────────────────────────

    def wait_for_release(self, timeout: Optional[float] = None) -> bool:
        """
        Block until the kill-switch is released (or timeout expires).
        Returns True if released, False if timeout.
        """
        if not self._engaged.is_set():
            return True
        # Wait for clear — poll since Event.wait() waits for set, not clear
        deadline = time.time() + (timeout or float("inf"))
        while self._engaged.is_set():
            if time.time() > deadline:
                return False
            time.sleep(0.05)
        return True

    # ── status ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            return {
                "engaged": self._engaged.is_set(),
                "reason": self._reason,
                "engaged_at": self._engaged_at,
                "released_at": self._released_at,
                "operator_id": self._operator_id,
                "audit_entries": len(self._audit_log),
            }

    def audit_log(self) -> list[dict]:
        with self._lock:
            return list(self._audit_log)

    def __repr__(self) -> str:
        state = "ENGAGED" if self.is_engaged else "RELEASED"
        return f"KillSwitch({state}, reason={self._reason!r})"
