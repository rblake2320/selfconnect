"""
__main__.py — ClaudeGo entry point.

Usage:
    python -m claudego
    python -m claudego --port 8080
    python -m claudego --dry-run
    python -m claudego --approve-all
    python -m claudego --no-browser
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import threading
import webbrowser

# ── Path setup — work from selfconnect/ root ──────────────────────────────────
_ROOT = pathlib.Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claudego",
        description="ClaudeGo — Local approval dashboard for Claude Code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python -m claudego                    # start on localhost:9090, open browser
  python -m claudego --port 8080        # different port
  python -m claudego --dry-run          # detect prompts, don't inject
  python -m claudego --approve-all      # auto-approve everything (use carefully)
  python -m claudego --no-browser       # don't open browser automatically
        """,
    )
    p.add_argument("--port", type=int, default=9090, metavar="PORT",
                   help="Dashboard port (default: 9090)")
    p.add_argument("--host", default="127.0.0.1",
                   help="Dashboard bind address (default: 127.0.0.1)")
    p.add_argument("--dry-run", action="store_true",
                   help="Detect prompts but don't inject y/n")
    p.add_argument("--approve-all", action="store_true",
                   help="Auto-approve all prompts (overrides rules)")
    p.add_argument("--deny-all", action="store_true",
                   help="Auto-deny all prompts (overrides rules)")
    p.add_argument("--poll", type=float, default=2.0, metavar="SECONDS",
                   help="Terminal poll interval in seconds (default: 2.0)")
    p.add_argument("--no-browser", action="store_true",
                   help="Don't open the dashboard in a browser automatically")
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.approve_all and args.deny_all:
        parser.error("--approve-all and --deny-all are mutually exclusive")

    # ── Build scanner ─────────────────────────────────────────────────────────
    try:
        from approval_partner import PartnerConfig  # type: ignore[import]
    except ImportError as exc:
        sys.exit(
            f"[claudego] ERROR: could not import approval_partner — {exc}\n"
            "Run from the selfconnect/ directory: python -m claudego"
        )

    default_action = "escalate"
    if args.approve_all:
        default_action = "approve"
    elif args.deny_all:
        default_action = "deny"

    cfg = PartnerConfig(
        default_action=default_action,
        dry_run=args.dry_run,
        poll_interval=args.poll,
        verbose=False,  # dashboard handles all output
    )

    from claudego.dashboard import app, set_scanner  # type: ignore[import]
    from claudego.scanner import Scanner  # type: ignore[import]

    scanner = Scanner(cfg=cfg, poll_interval=args.poll, dry_run=args.dry_run)
    set_scanner(scanner)

    # ── Start scanner thread ──────────────────────────────────────────────────
    scanner_thread = threading.Thread(
        target=scanner.run,
        name="claudego-scanner",
        daemon=True,
    )
    scanner_thread.start()
    print(f"[claudego] Scanner started (poll={args.poll}s, action={default_action})")

    # ── Open browser ──────────────────────────────────────────────────────────
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        def _open():
            import time
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"[claudego] Dashboard → {url}")
    print("[claudego] Press Ctrl+C to stop")

    # ── Start uvicorn ─────────────────────────────────────────────────────────
    try:
        import uvicorn  # type: ignore[import]
    except ImportError:
        sys.exit(
            "[claudego] ERROR: uvicorn not installed.\n"
            "Install: pip install uvicorn[standard] fastapi"
        )

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",  # suppress uvicorn noise
    )


if __name__ == "__main__":
    main()
