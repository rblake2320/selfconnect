"""
ClaudeGo — Local approval dashboard for Claude Code.

Monitors Claude Code terminals, auto-approves safe tool calls,
surfaces unknown ones in a real-time web dashboard.

Usage:
    python -m claudego          # start dashboard on localhost:9090
    python -m claudego --port 8080
    python -m claudego --dry-run
"""

__version__ = "0.1.0"
