"""Strict, portable capability manifest types."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

SKILL_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
SCHEMA_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
INSTRUCTION_LIKE = re.compile(
    r"(?i)\b(ignore|disregard|override)\b.{0,40}\b(previous|prior|system|permission|policy)\b"
    r"|\b(system prompt|developer message|call execute|invoke command)\b"
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class SkillManifest:
    name: str
    version: str
    description: str
    adapter: str
    permissions: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    verification: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    provenance: str = "builtin"
    manifest_digest: str = ""

    def __post_init__(self) -> None:
        if not SKILL_NAME.fullmatch(self.name):
            raise ValueError(f"invalid skill name: {self.name!r}")
        if not self.version.strip() or not self.description.strip():
            raise ValueError("skill version and description are required")
        if len(self.description) > 1_000 or any(ord(char) < 32 for char in self.description):
            raise ValueError("skill description contains invalid or excessive text")
        if self.provenance != "builtin" and INSTRUCTION_LIKE.search(self.description):
            raise ValueError("external skill description contains instruction-like text")
        if not SKILL_NAME.fullmatch(self.adapter):
            raise ValueError(f"invalid adapter name: {self.adapter!r}")
        if self.input_schema.get("type", "object") != "object":
            raise ValueError("input_schema must describe an object")
        properties = self.input_schema.get("properties", {})
        required = self.input_schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError("invalid input_schema properties or required fields")
        unknown_required = set(required) - set(properties)
        if unknown_required:
            raise ValueError(f"required fields missing schemas: {sorted(unknown_required)}")
        for name, schema in properties.items():
            if not isinstance(schema, dict) or schema.get("type") not in SCHEMA_TYPES:
                raise ValueError(f"unsupported schema for input {name!r}")
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("duplicate permissions are not allowed")
        if self.manifest_digest and self.manifest_digest != self.digest():
            raise ValueError(f"manifest digest mismatch for {self.name}")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "adapter": self.adapter,
            "permissions": list(self.permissions),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "verification": list(self.verification),
            "tags": list(self.tags),
            "provenance": self.provenance,
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.unsigned_dict()).encode("utf-8")).hexdigest()

    def public_dict(self) -> dict[str, Any]:
        result = self.unsigned_dict()
        result["manifest_digest"] = self.manifest_digest or self.digest()
        result["description_trust"] = (
            "trusted_builtin" if self.provenance == "builtin" else "untrusted_external"
        )
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SkillManifest:
        allowed = {
            "name", "version", "description", "adapter", "permissions",
            "input_schema", "output_schema", "verification", "tags",
            "provenance", "manifest_digest",
            "description_trust",
        }
        unexpected = set(value) - allowed
        if unexpected:
            raise ValueError(f"unexpected manifest fields: {sorted(unexpected)}")
        return cls(
            name=str(value.get("name", "")),
            version=str(value.get("version", "")),
            description=str(value.get("description", "")),
            adapter=str(value.get("adapter", "")),
            permissions=tuple(map(str, value.get("permissions", []))),
            input_schema=dict(value.get("input_schema", {})),
            output_schema=dict(value.get("output_schema", {})),
            verification=tuple(map(str, value.get("verification", []))),
            tags=tuple(map(str, value.get("tags", []))),
            provenance=str(value.get("provenance", "external")),
            manifest_digest=str(value.get("manifest_digest", "")),
        )


def validate_inputs(manifest: SkillManifest, arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("skill arguments must be an object")
    schema = manifest.input_schema
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    missing = [name for name in required if name not in arguments]
    if missing:
        raise ValueError(f"missing required skill arguments: {missing}")
    if schema.get("additionalProperties") is False:
        unexpected = set(arguments) - set(properties)
        if unexpected:
            raise ValueError(f"unexpected skill arguments: {sorted(unexpected)}")
    python_types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for name, value in arguments.items():
        expected = properties.get(name, {}).get("type")
        if expected and not isinstance(value, python_types[expected]):
            raise ValueError(f"skill argument {name!r} must be {expected}")
