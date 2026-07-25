"""Built-in manifests. Adapters are bound by the host runtime."""

from __future__ import annotations

from .models import SkillManifest


def _object(properties=None, required=None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


BUILTIN_SKILLS = (
    SkillManifest(
        "selfconnect.doctor", "1.0.0", "Inspect installed SelfConnect platform capabilities.",
        "doctor", ("observe.system",), _object(), tags=("health", "diagnostics", "capabilities"),
    ),
    SkillManifest(
        "selfconnect.mesh-roster", "1.0.0", "List registered AI mesh roles and identities.",
        "mesh-roster", ("read.mesh",), _object(), tags=("agents", "roles", "identity", "mesh"),
    ),
    SkillManifest(
        "selfconnect.verify-window", "1.0.0", "Verify a mesh role's HWND, process, class, and title.",
        "verify-role-window", ("read.window",),
        _object({"role": {"type": "string"}}, ["role"]),
        verification=("output-ok",), tags=("window", "guard", "identity", "verify"),
    ),
    SkillManifest(
        "selfconnect.read-window", "1.0.0", "Read a verified role window through UIA or Win32.",
        "read-role-window", ("read.window",),
        _object({"role": {"type": "string"}}, ["role"]),
        verification=("output-ok",), tags=("window", "uia", "text", "terminal"),
    ),
    SkillManifest(
        "selfconnect.capture-window", "1.0.0", "Capture and optionally OCR a verified role window.",
        "capture-role-window", ("capture.window",),
        _object({
            "role": {"type": "string"},
            "ocr": {"type": "boolean"},
        }, ["role"]),
        verification=("output-ok",), tags=("window", "ocr", "vision", "screen"),
    ),
    SkillManifest(
        "selfconnect.send-message", "1.0.0", "Send guarded text to a registered mesh role.",
        "send-role-message", ("input.window",),
        _object({
            "role": {"type": "string"},
            "text": {"type": "string"},
            "submit": {"type": "boolean"},
        }, ["role", "text"]),
        verification=("output-ok",), tags=("message", "send", "terminal", "agent"),
    ),
    SkillManifest(
        "selfconnect.file-read", "1.0.0", "Read a repository-bounded UTF-8 file.",
        "file-read", ("read.file",),
        _object({"path": {"type": "string"}}, ["path"]),
        verification=("output-ok",), tags=("file", "read", "repository"),
    ),
    SkillManifest(
        "selfconnect.file-write", "1.0.0", "Write a repository-bounded UTF-8 file.",
        "file-write", ("write.file",),
        _object({
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, ["path", "content"]),
        verification=("output-ok",), tags=("file", "write", "repository"),
    ),
    SkillManifest(
        "selfconnect.command", "1.0.0", "Run a bounded argv-form command through runtime policy.",
        "command", ("execute.command",),
        _object({"argv": {"type": "array"}}, ["argv"]),
        verification=("output-ok",), tags=("cli", "shell", "command", "powershell"),
    ),
)
