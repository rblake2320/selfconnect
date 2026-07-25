"""Immutable per-run capability authority."""

from __future__ import annotations

from dataclasses import dataclass


class PermissionDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Authority:
    principal: str
    permissions: frozenset[str] = frozenset()

    def missing(self, required: tuple[str, ...]) -> list[str]:
        return sorted(set(required) - self.permissions)

    def require(self, required: tuple[str, ...]) -> None:
        missing = self.missing(required)
        if missing:
            raise PermissionDenied(f"missing capability permissions: {missing}")

    def can(self, required: tuple[str, ...]) -> bool:
        return not self.missing(required)
