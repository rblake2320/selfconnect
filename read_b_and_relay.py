"""Read B's terminal output, extract the python -c line, execute it so B replies to A."""
import sys, os, time, subprocess
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import get_text_uia

B_HWND = 0x01fa0d74

print("Reading Agent B terminal...")
text = get_text_uia(B_HWND)
if not text:
    print("No text captured from B")
    sys.exit(1)

# Print last 2000 chars so we can see what B said
print("\n--- B's terminal (last 2000 chars) ---")
print(text[-2000:])
print("--- end ---\n")

# Look for a python -c line in B's output
lines = text.splitlines()
oneliner = None
for line in reversed(lines):
    stripped = line.strip()
    if stripped.startswith('python -c') or stripped.startswith('python3 -c'):
        oneliner = stripped
        break

if oneliner:
    print(f"\nFound B's one-liner:\n  {oneliner}\n")
    print("Executing it (so B's send_string fires to A)...")
    result = subprocess.run(
        oneliner,
        shell=True,
        capture_output=True,
        text=True,
        cwd="C:/Users/techai/PKA testing/selfconnect"
    )
    print("stdout:", result.stdout)
    print("stderr:", result.stderr)
    print("returncode:", result.returncode)
else:
    print("No python -c one-liner found yet. B may still be thinking — try again in a few seconds.")
