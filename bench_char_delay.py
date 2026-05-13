"""bench_char_delay.py — Empirically measure minimum reliable char_delay for ConPTY.

Sends a known payload via send_string() at various delays, then reads the result
back from a temp file. Outputs a CSV so you can pick the minimum delay with 0%
drop rate as _TURBO_DELAY.

Usage:
    python bench_char_delay.py [--hwnd <hwnd>] [--trials 5] [--out bench_results.csv]

If --hwnd is not given, the script finds the first Windows Terminal window.
The target terminal MUST be running cmd.exe (not PowerShell or Claude Code).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from self_connect import WindowTarget, find_target, list_windows, send_string

# ── Payload generator ─────────────────────────────────────────────────────────

def _make_payload(length: int, seed: str = "BENCH") -> str:
    """Generate a deterministic printable ASCII payload of exactly `length` chars.
    Uses only characters safe for cmd.exe echo: A-Z 0-9 (no special chars).
    """
    return (seed * ((length // len(seed)) + 2))[:length]


# ── Single trial ──────────────────────────────────────────────────────────────

def _run_trial(target: WindowTarget, payload: str, out_file: str, delay: float) -> str:
    """Send `echo <payload> > out_file` and return what was written.

    Returns the file contents (stripped), or "" on failure.
    """
    # Clear the file first
    try:
        Path(out_file).write_text("", encoding="ascii")
    except Exception:
        pass

    cmd = f"echo {payload}>{out_file}\r"
    send_string(target, cmd, char_delay=delay)

    # Wait for cmd.exe to execute (up to 3s)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        time.sleep(0.1)
        try:
            content = Path(out_file).read_text(encoding="ascii", errors="replace").strip()
            if content:
                return content
        except Exception:
            pass
    return ""


# ── Analyse result ─────────────────────────────────────────────────────────────

def _analyse(sent: str, received: str) -> tuple[int, int, float]:
    """Return (chars_sent, chars_received, drop_rate).

    `received` may have extra cmd.exe decorations (leading space from echo).
    We look for the longest common substring overlap.
    """
    sent_clean = sent.strip()
    recv_clean = received.strip()

    # cmd.exe echo adds a space before the text: "echo ABCD" → " ABCD"
    # Also strip leading/trailing whitespace
    chars_sent = len(sent_clean)
    # Count how many of sent's chars appear in received (in order, subsequence)
    i = j = 0
    matched = 0
    while i < len(sent_clean) and j < len(recv_clean):
        if sent_clean[i] == recv_clean[j]:
            matched += 1
            i += 1
        j += 1

    chars_received = matched
    drop_rate = 0.0 if chars_sent == 0 else (chars_sent - chars_received) / chars_sent
    return chars_sent, chars_received, drop_rate


# ── Main ──────────────────────────────────────────────────────────────────────

DELAYS_MS = [0, 1, 2, 5, 10, 20, 50]
PAYLOAD_SIZES = [50, 100, 200, 500]


def run_benchmark(target: WindowTarget, trials: int, out_path: str) -> None:
    # Create a temp file for output
    tmp_dir = tempfile.mkdtemp()
    out_file = os.path.join(tmp_dir, "bench_out.txt")

    rows: list[dict] = []
    total = len(DELAYS_MS) * len(PAYLOAD_SIZES) * trials
    done = 0

    print(f"Target: hwnd={target.hwnd} title={target.title!r}")
    print(f"Trials: {trials} | Combos: {len(DELAYS_MS)} delays x {len(PAYLOAD_SIZES)} sizes")
    print(f"Total sends: {total}")
    print()

    for delay_ms in DELAYS_MS:
        delay_s = delay_ms / 1000.0
        for size in PAYLOAD_SIZES:
            payload = _make_payload(size)
            for trial in range(1, trials + 1):
                received = _run_trial(target, payload, out_file, delay_s)
                sent_n, recv_n, drop = _analyse(payload, received)
                row = {
                    "delay_ms": delay_ms,
                    "payload_len": size,
                    "trial": trial,
                    "chars_sent": sent_n,
                    "chars_received": recv_n,
                    "drop_rate": f"{drop:.4f}",
                    "status": "OK" if drop == 0.0 else "DROP",
                }
                rows.append(row)
                done += 1
                status = "✓" if drop == 0.0 else f"✗ ({drop:.1%} drop)"
                print(f"  [{done:3d}/{total}] delay={delay_ms:3d}ms  size={size:4d}  trial={trial}  {status}")

                # Brief pause between trials to let cmd settle
                time.sleep(0.2)

    # Write CSV
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nResults written to: {out_path}")

    # Summary: find minimum reliable delay
    print("\n── Summary ────────────────────────────────────────────────────────")
    print(f"{'delay_ms':>10}  {'any_drop':>10}  {'max_drop':>12}  {'verdict':>10}")
    for delay_ms in DELAYS_MS:
        subset = [r for r in rows if r["delay_ms"] == delay_ms]
        any_drop = any(float(r["drop_rate"]) > 0 for r in subset)
        max_drop = max(float(r["drop_rate"]) for r in subset)
        verdict = "RELIABLE" if not any_drop else "DROPS"
        print(f"{delay_ms:>10}  {any_drop!r:>10}  {max_drop:>12.4f}  {verdict:>10}")

    reliable = [d for d in DELAYS_MS if not any(float(r["drop_rate"]) > 0 for r in rows if r["delay_ms"] == d)]
    if reliable:
        rec = min(reliable)
        print(f"\n→ Recommended _TURBO_DELAY = {rec}ms  ({rec/1000.0:.3f}s)")
    else:
        print("\n→ No delay was fully reliable at all sizes — try reducing payload size or check terminal state")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark ConPTY char_delay reliability")
    parser.add_argument("--hwnd", type=int, default=None, help="Target HWND (cmd.exe in Windows Terminal)")
    parser.add_argument("--trials", type=int, default=5, help="Trials per combination (default 5)")
    parser.add_argument("--out", default="bench_results.csv", help="Output CSV path")
    args = parser.parse_args()

    if args.hwnd:
        target = next((w for w in list_windows() if w.hwnd == args.hwnd), None)
        if target is None:
            print(f"ERROR: No window with hwnd={args.hwnd}", file=sys.stderr)
            sys.exit(1)
    else:
        target = find_target("cmd")
        if target is None:
            # Try generic terminal
            target = find_target("Windows PowerShell")
        if target is None:
            wins = [w for w in list_windows() if "terminal" in w.title.lower() or "cmd" in w.title.lower()]
            target = wins[0] if wins else None
        if target is None:
            print("ERROR: No cmd.exe / Windows Terminal window found.", file=sys.stderr)
            print("Open a Windows Terminal running cmd.exe and try again.", file=sys.stderr)
            sys.exit(1)
        print(f"Auto-selected: {target.title!r} (hwnd={target.hwnd})")

    run_benchmark(target, args.trials, args.out)


if __name__ == "__main__":
    main()
