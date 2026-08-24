"""Owned Windows UI fixture for live SelfConnect observation proofs."""

from __future__ import annotations

import argparse
import json
import os
import tkinter as tk
from tkinter import ttk


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--sentinel", required=True)
    args = parser.parse_args()

    root = tk.Tk()
    root.title(args.title)
    root.geometry("960x620+120+120")
    root.configure(background="white")
    root.attributes("-topmost", True)

    heading = tk.Label(
        root,
        text="SELFCONNECT OWNED LIVE OBSERVATION FIXTURE",
        font=("Segoe UI", 24, "bold"),
        foreground="black",
        background="white",
    )
    heading.pack(pady=(45, 20))

    sentinel = tk.Label(
        root,
        text=args.sentinel,
        font=("Consolas", 34, "bold"),
        foreground="black",
        background="white",
    )
    sentinel.pack(pady=20)

    entry = ttk.Entry(root, font=("Consolas", 20), width=42)
    entry.insert(0, args.sentinel)
    entry.pack(pady=20)

    button = ttk.Button(root, text="Owned Test Button")
    button.pack(pady=20)

    footer = tk.Label(
        root,
        text="Local fixture only - no browser, cloud service, or external API",
        font=("Segoe UI", 14),
        foreground="black",
        background="white",
    )
    footer.pack(pady=20)

    root.update_idletasks()
    root.lift()
    root.focus_force()
    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "title": args.title,
                "sentinel": args.sentinel,
            }
        ),
        flush=True,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
