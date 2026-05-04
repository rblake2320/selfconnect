"""
Gemini Bridge — Interactive CLI for cross-AI orchestration via Antigravity.

Demonstrates full programmatic control of Google's Antigravity IDE using
the SelfConnect antigravity_controller SDK. Zero API keys — pure Win32.

Usage:
    python gemini_bridge.py --demo
    python gemini_bridge.py --interactive
    python gemini_bridge.py --monitor
    python gemini_bridge.py --switch-model "Gemini 2.5 Pro"
    python gemini_bridge.py --status
"""

import argparse
import sys
import time

from antigravity_controller import (
    AntigravityMonitor,
    AntigravitySession,
    chat,
    connect,
    get_model,
    list_buttons,
    new_chat,
    set_model,
)


def print_header(title: str) -> None:
    print(f"\n{'=' * 50}")
    print(f"  {title}")
    print(f"{'=' * 50}\n")


def print_session(session: AntigravitySession) -> None:
    print(f"  HWND:       0x{session.hwnd:x}")
    print(f"  Render:     0x{session.chrome_hwnd:x}")
    print(f"  PID:        {session.pid}")
    print(f"  Model:      {session.model or '(unknown)'}")
    print(f"  Title:      {session.title[:60]}")
    print(f"  Standalone: {session.is_standalone}")
    print(f"  UIA Ready:  {session.uia_ready}")
    print()


def safe_connect() -> AntigravitySession:
    """Connect to Antigravity with a clear error message on failure."""
    try:
        session = connect()
        return session
    except RuntimeError as e:
        print(f"\n[ERROR] {e}")
        print("\nMake sure Antigravity is running and fully loaded.")
        sys.exit(1)
    except ImportError as e:
        print(f"\n[ERROR] Missing dependency: {e}")
        print("Install with: pip install comtypes pywinauto")
        sys.exit(1)


def run_status(session: AntigravitySession) -> None:
    """Print session info and all available buttons."""
    print_header("Antigravity Status")
    print_session(session)
    model = get_model(session)
    print(f"  Live model: {model or '(could not read)'}\n")
    buttons = list_buttons(session)
    print(f"  Buttons ({len(buttons)}):")
    for b in buttons:
        print(f"    - {b}")
    print()


def run_demo(session: AntigravitySession, timeout: int, start_fresh: bool) -> None:
    """Run a scripted multi-turn conversation demonstrating cross-AI orchestration."""
    print_header("SelfConnect x Antigravity Demo")
    print(f"Connected: {session}\n")

    if start_fresh:
        print("[Setup] Starting fresh conversation...")
        if new_chat(session):
            print("[Setup] New chat started.\n")
            time.sleep(1.5)
        else:
            print("[Setup] WARNING: Could not click New Chat. Continuing anyway.\n")

    turns = [
        ("What model are you and what can you help with?",),
        ("Write a Python function that checks if a string is a palindrome.",),
        ("Can you add type hints and a docstring to that function?",),
    ]

    for i, (prompt,) in enumerate(turns, 1):
        model_before = get_model(session) or "(unknown)"
        print(f"[Turn {i}] Claude -> Gemini  (model: {model_before})")
        print(f"  Sent: \"{prompt}\"")
        print("  ...waiting...")

        try:
            response = chat(session, prompt, timeout=timeout)
            # Trim long responses for display
            lines = response.strip().splitlines()
            if len(lines) > 12:
                display = "\n".join(lines[:10])
                display += f"\n  ... ({len(lines) - 10} more lines)"
            else:
                display = response.strip()
            print(f"  Gemini: {display}\n")
        except TimeoutError:
            print(f"  [TIMEOUT] No response within {timeout}s.\n")
        except RuntimeError as e:
            print(f"  [ERROR] {e}\n")

        # Brief pause between turns
        if i < len(turns):
            time.sleep(2.0)

    print(f"{'=' * 50}")
    print(f"  Demo complete. {len(turns)} turns, all via Win32. Zero API calls.")
    print(f"{'=' * 50}\n")


def run_interactive(session: AntigravitySession, timeout: int) -> None:
    """REPL mode — type messages and get Gemini responses."""
    print_header("Interactive Mode")
    print(f"Connected: {session}")
    print("Type your message and press Enter. Type 'quit' or Ctrl+C to exit.\n")

    while True:
        try:
            prompt = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nExiting.")
            break

        if not prompt:
            continue
        if prompt.lower() in ("quit", "exit", "q"):
            print("Exiting.")
            break

        print("  ...waiting...")
        try:
            response = chat(session, prompt, timeout=timeout)
            print(f"Gemini: {response.strip()}\n")
        except TimeoutError:
            print(f"  [TIMEOUT] No response within {timeout}s.\n")
        except RuntimeError as e:
            print(f"  [ERROR] {e}\n")


def run_monitor(session: AntigravitySession) -> None:
    """Start AntigravityMonitor and print events until Ctrl+C."""
    print_header("Monitor Mode")
    print(f"Connected: {session}")
    print("Watching for Gemini responses... Press Ctrl+C to stop.\n")

    monitor = AntigravityMonitor(session, poll=1.5)
    monitor.on("response", lambda r: print(f"[RESPONSE] {r[:200]}"))
    monitor.on("model_changed", lambda m: print(f"[MODEL CHANGED] {m}"))
    monitor.on("error", lambda e: print(f"[ERROR] {e}"))
    monitor.start()

    try:
        while monitor.is_running():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\nStopping monitor...")
    finally:
        monitor.stop(timeout=3.0)
        print("Monitor stopped.")


def run_switch_model(session: AntigravitySession, target: str) -> None:
    """Switch the model in Antigravity and confirm."""
    print_header("Model Switch")
    current = get_model(session)
    print(f"  Current model: {current or '(unknown)'}")
    print(f"  Target model:  {target}")
    print("  Switching...")

    if set_model(session, target):
        new = get_model(session)
        print(f"  Success! Now using: {new}")
    else:
        print(f"  FAILED: Could not switch to '{target}'.")
        print("  Available buttons might help diagnose:")
        for b in list_buttons(session):
            if "model" in b.lower() or "select" in b.lower():
                print(f"    - {b}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gemini Bridge — Cross-AI orchestration via Antigravity"
    )
    parser.add_argument("--demo", action="store_true",
                        help="Run scripted multi-turn demo")
    parser.add_argument("--interactive", action="store_true",
                        help="Interactive REPL chat mode")
    parser.add_argument("--monitor", action="store_true",
                        help="Watch for responses in real-time")
    parser.add_argument("--switch-model", metavar="NAME",
                        help="Switch Antigravity to a different model")
    parser.add_argument("--status", action="store_true",
                        help="Print session info and buttons")
    parser.add_argument("--new-chat", action="store_true",
                        help="Start a fresh conversation before running")
    parser.add_argument("--timeout", type=int, default=45,
                        help="Response timeout in seconds (default: 45)")

    args = parser.parse_args()

    # Must pick at least one mode
    if not any([args.demo, args.interactive, args.monitor,
                args.switch_model, args.status]):
        parser.print_help()
        sys.exit(0)

    # Connect
    print("Connecting to Antigravity...")
    session = safe_connect()
    print(f"Connected: {session}\n")

    # Handle --new-chat before any mode (except status)
    if args.new_chat and not args.status:
        print("Starting fresh conversation...")
        if new_chat(session):
            print("New chat started.\n")
            time.sleep(1.0)
        else:
            print("WARNING: Could not start new chat.\n")

    # Dispatch
    if args.status:
        run_status(session)
    elif args.switch_model:
        run_switch_model(session, args.switch_model)
    elif args.demo:
        run_demo(session, args.timeout, start_fresh=False)
    elif args.interactive:
        run_interactive(session, args.timeout)
    elif args.monitor:
        run_monitor(session)


if __name__ == "__main__":
    main()
