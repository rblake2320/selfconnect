"""
Watch Agent B's terminal. Auto-execute any python -c lines B generates.
Runs in a loop — proves B is the initiator, not Agent A.
"""
import sys, os, time, subprocess, re
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import get_text_uia

B_HWND = 0x01fa0d74
POLL_INTERVAL = 2  # seconds
seen_commands = set()

print(f"[watcher] Monitoring Agent B at 0x{B_HWND:x}...")
print("[watcher] Will auto-execute any python -c lines B generates.")
print("[watcher] Press Ctrl+C to stop.\n")

while True:
    try:
        text = get_text_uia(B_HWND) or ""
        lines = text.splitlines()

        # Reconstruct wrapped lines: join continuation lines until we have a complete command
        full_lines = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('python -c "') or line.startswith("python -c '"):
                # Accumulate wrapped continuation until closing quote
                combined = line
                while not (combined.count('"') % 2 == 0 or combined.endswith('")')):
                    i += 1
                    if i >= len(lines):
                        break
                    combined += lines[i].strip()
                full_lines.append(combined)
            else:
                full_lines.append(line)
            i += 1

        for cmd in full_lines:
            cmd = cmd.strip()
            if not (cmd.startswith('python -c "') or cmd.startswith("python -c '")):
                continue
            if cmd in seen_commands:
                continue
            seen_commands.add(cmd)

            print(f"\n[watcher] NEW command from B:\n  {cmd[:120]}...")
            print("[watcher] Executing...")
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd="C:/Users/techai/PKA testing/selfconnect"
            )
            if result.returncode == 0:
                print(f"[watcher] OK — {result.stdout.strip()}")
            else:
                print(f"[watcher] ERROR: {result.stderr.strip()[:200]}")

        time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[watcher] Stopped.")
        break
    except Exception as e:
        print(f"[watcher] poll error: {e}")
        time.sleep(POLL_INTERVAL)
