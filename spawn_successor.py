"""Disabled legacy successor launcher.

This entry point previously spawned a terminal, injected an unsigned role
briefing, and rewrote ``mesh_config.py``.  Terminal text is not migration
authority, so the legacy path now fails closed.  Authenticated successors must
be created through :class:`self_connect.MigrationCoordinator`, which emits and
verifies SelfConnect migration-v2 manifests before committing a handoff.
"""
from __future__ import annotations

import json

REJECTION = {
    "ok": False,
    "status": "REJECTED",
    "reason": "legacy unauthenticated successor spawning is disabled",
    "required_path": "self_connect.MigrationCoordinator with migration-v2 verification",
}


def main() -> int:
    """Reject the legacy launch without spawning, sending, or mutating files."""
    print(json.dumps(REJECTION, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
