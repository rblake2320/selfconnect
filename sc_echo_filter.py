"""
sc_echo_filter.py — Reusable echo-filtered readback helper for SelfConnect.

Promotes EchoFilter from experiment into runtime so MessageListener,
verify_delivery, and mesh heartbeat/readback logic can use it without
depending on raw terminal text.

Public API:

    classify(delta, nonce) -> EchoClassification
    build_record(...)      -> ReadbackRecord
    EchoClassification     (enum: ECHO_ONLY, EXTERNAL_OUTPUT, MIXED, NO_SIGNAL, UNKNOWN)
    ReadbackRecord         (dataclass: all fields for a single classify-and-record call)

Does NOT perform UIA reads, window enumeration, or Win32 calls.
Those remain in self_connect.py / experiments/win32_probe/.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum


class EchoClassification(str, Enum):
    """Classification of a terminal text delta relative to a known injected nonce."""

    ECHO_ONLY       = "echo_only"        # delta contains only local injected echo
    EXTERNAL_OUTPUT = "external_output"  # delta contains only external output (no echo)
    MIXED           = "mixed"            # delta contains both echo and external output
    NO_SIGNAL       = "no_signal"        # delta is empty / whitespace only
    UNKNOWN         = "unknown"          # nonce is empty; can't determine


_STRIP = "\r\n \t"


def classify(delta: str, nonce: str) -> EchoClassification:
    """Classify *delta* as echo, external output, or mixed.

    Args:
        delta: Text observed in the terminal readback since the last baseline.
        nonce: The exact string that was injected via send_string / WM_CHAR.

    Returns:
        EchoClassification enum member.

    Rules:
        - Empty delta                          -> NO_SIGNAL
        - Empty nonce                          -> UNKNOWN
        - Nonce found, nothing else            -> ECHO_ONLY
        - Nonce found, other text present      -> MIXED
        - Nonce not found, delta has content   -> EXTERNAL_OUTPUT
    """
    if not delta.strip(_STRIP):
        return EchoClassification.NO_SIGNAL

    if not nonce:
        return EchoClassification.UNKNOWN

    stripped = delta.lstrip(_STRIP)

    if nonce not in stripped:
        return EchoClassification.EXTERNAL_OUTPUT

    # Nonce is present — determine whether there is also external content.
    # Remove the nonce from the stripped delta and check if meaningful text remains.
    remainder = stripped.replace(nonce, "", 1).strip(_STRIP)
    # Strip terminal artefacts that are not real output: lone quotes left after
    # nonce removal (cmd echoes 'NONCE' not recognized), prompt lines (C:\>),
    # and whitespace-only padding.
    _ARTEFACTS = ("''", '""', "\r", "\n")
    cleaned = remainder
    for a in _ARTEFACTS:
        cleaned = cleaned.replace(a, "")
    cleaned = cleaned.strip(_STRIP)

    if cleaned:
        return EchoClassification.MIXED
    return EchoClassification.ECHO_ONLY


def split_echo(delta: str, nonce: str) -> tuple[str, str]:
    """Return (echo_part, output_part) extracted from *delta*.

    echo_part  : the portion that is the injected nonce (or empty string).
    output_part: the remainder after removing the nonce (may be empty).

    Does not raise; returns ("", delta.strip()) if nonce is not found.
    """
    if not delta.strip(_STRIP) or not nonce:
        return ("", delta.strip(_STRIP))

    stripped = delta.lstrip(_STRIP)

    if nonce not in stripped:
        return ("", stripped)

    pos = stripped.index(nonce)
    echo_part   = nonce
    before      = stripped[:pos].strip(_STRIP)
    after       = stripped[pos + len(nonce):].strip(_STRIP)
    output_part = (before + " " + after).strip() if before else after
    return (echo_part, output_part)


@dataclass
class ReadbackRecord:
    """Structured record for one echo-filtered readback observation.

    Callers fill this via build_record(); it can be serialised to JSON or
    passed directly to MessageListener / verify_delivery / mesh heartbeat.
    """

    nonce:          str = ""
    sent_hash:      str = ""   # SHA-256 hex of injected nonce bytes (or sent_text bytes)
    observed_hash:  str = ""   # SHA-256 hex of raw delta bytes

    echo_part:   str = ""
    output_part: str = ""
    classification: EchoClassification = EchoClassification.UNKNOWN

    hwnd:         int   = 0
    pid:          int   = 0
    readback_method: str = ""  # "TextChanged_event" | "TextPattern_poll" | "get_text_uia" | ""

    timestamp_send:  float = 0.0
    timestamp_recv:  float = 0.0
    latency_ms:      float = 0.0


def build_record(
    *,
    delta: str,
    nonce: str,
    sent_text: str = "",
    hwnd: int = 0,
    pid: int = 0,
    readback_method: str = "",
    timestamp_send: float = 0.0,
    timestamp_recv: float = 0.0,
) -> ReadbackRecord:
    """Classify *delta* and return a fully populated ReadbackRecord.

    Args:
        delta:           Text observed in terminal readback since last baseline.
        nonce:           Injected nonce string (used for echo detection).
        sent_text:       Full text that was injected (defaults to nonce if empty).
        hwnd:            Target window handle.
        pid:             Target process ID.
        readback_method: How the delta was obtained ("TextChanged_event", etc.).
        timestamp_send:  time.time() at injection (0 = unknown).
        timestamp_recv:  time.time() when delta was first observed (0 = now).
    """
    if not timestamp_recv:
        timestamp_recv = time.time()

    payload = (sent_text or nonce).encode()
    echo_part, output_part = split_echo(delta, nonce)

    rec = ReadbackRecord(
        nonce          = nonce,
        sent_hash      = hashlib.sha256(payload).hexdigest(),
        observed_hash  = hashlib.sha256(delta.encode()).hexdigest(),
        echo_part      = echo_part[:256],
        output_part    = output_part[:512],
        classification = classify(delta, nonce),
        hwnd           = hwnd,
        pid            = pid,
        readback_method = readback_method,
        timestamp_send  = timestamp_send,
        timestamp_recv  = timestamp_recv,
        latency_ms      = (timestamp_recv - timestamp_send) * 1000 if timestamp_send else 0.0,
    )
    return rec
