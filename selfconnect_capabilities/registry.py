"""Verified manifest loading and progressive skill discovery."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import SkillManifest
from .permissions import Authority

TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]+")


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}

    def register(self, manifest: SkillManifest, *, replace: bool = False) -> None:
        if manifest.name in self._skills and not replace:
            raise ValueError(f"skill already registered: {manifest.name}")
        self._skills[manifest.name] = manifest

    def load_directory(self, root: Path, *, require_digest: bool = True) -> int:
        count = 0
        if not root.exists():
            return count
        for path in sorted(root.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            manifest = SkillManifest.from_dict(value)
            if require_digest and not manifest.manifest_digest:
                raise ValueError(f"external manifest is not digest-pinned: {path}")
            self.register(manifest)
            count += 1
        return count

    def get(self, name: str) -> SkillManifest:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {name}") from exc

    def all(self) -> list[SkillManifest]:
        return [self._skills[name] for name in sorted(self._skills)]

    def discover(
        self,
        query: str,
        authority: Authority,
        *,
        limit: int = 5,
        include_unavailable: bool = True,
    ) -> list[dict]:
        words = set(TOKEN.findall(query.casefold()))
        ranked: list[tuple[int, str, SkillManifest]] = []
        for skill in self._skills.values():
            haystack = " ".join((skill.name, skill.description, *skill.tags)).casefold()
            tokens = set(TOKEN.findall(haystack))
            score = sum(4 if word in skill.name else 1 for word in words if word in tokens or word in haystack)
            if not words:
                score = 1
            if score:
                ranked.append((score, skill.name, skill))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        result = []
        for score, _, skill in ranked:
            missing = authority.missing(skill.permissions)
            if missing and not include_unavailable:
                continue
            result.append({
                "name": skill.name,
                "version": skill.version,
                "description": skill.description,
                "permissions": list(skill.permissions),
                "available": not missing,
                "missing_permissions": missing,
                "score": score,
                "manifest_digest": skill.manifest_digest or skill.digest(),
            })
            if len(result) >= max(1, min(limit, 20)):
                break
        return result
