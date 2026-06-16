"""Optional MCP server for SelfConnect.

Run with:

    selfconnect-mcp

Input delivery is disabled by default. Set ``SELFCONNECT_MCP_ALLOW_INPUT=1`` to
enable the ``send_text`` tool.
"""

from __future__ import annotations

import os
from typing import Any

import sc_cli


def _mcp_input_allowed() -> bool:
    return os.environ.get("SELFCONNECT_MCP_ALLOW_INPUT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def build_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "The MCP server requires the optional dependency: "
            "pip install selfconnect[mcp]"
        ) from exc

    server = FastMCP("SelfConnect")

    @server.tool()
    def doctor(include_windows: bool = False, query: str = "", limit: int = 20) -> dict[str, Any]:
        """Return SelfConnect version, Win32 capability probes, and optional windows."""
        return sc_cli.doctor_report(include_windows, query, limit)

    @server.tool()
    def list_windows(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """List visible windows, optionally filtered by title, exe, or class."""
        return sc_cli.list_window_records(query, limit)

    @server.tool()
    def read_window(hwnd: int, prefer_uia: bool = True) -> dict[str, Any]:
        """Read text from a window using UIA first, then child-window text fallback."""
        return sc_cli.read_window(hwnd, prefer_uia=prefer_uia)

    @server.tool()
    def capture_window(hwnd: int, path: str = "", crop: bool = True) -> dict[str, Any]:
        """Capture a window to PNG. Returns the saved path."""
        return sc_cli.capture_window(hwnd, path=path, crop=crop)

    @server.tool()
    def send_text(
        hwnd: int,
        text: str,
        submit: bool = False,
        char_delay: float = 0.05,
    ) -> dict[str, Any]:
        """Type text into a window. Disabled unless SELFCONNECT_MCP_ALLOW_INPUT=1."""
        return sc_cli.send_text_to_window(
            hwnd,
            text,
            submit=submit,
            char_delay=char_delay,
            allow_input=_mcp_input_allowed(),
            env_name="SELFCONNECT_MCP_ALLOW_INPUT",
        )

    return server


def main() -> int:
    try:
        server = build_server()
    except RuntimeError as exc:
        print(f"selfconnect-mcp: {exc}")
        return 1
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
