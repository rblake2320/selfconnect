"""Send mesh update to Agent-B."""
import sys, os, time
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_connect import list_windows, send_string

wins = list_windows()
b_hwnd = 0x1311316
b_w = next((w for w in wins if w.hwnd == b_hwnd), None)
if not b_w:
    print("Agent-B not found")
    sys.exit(1)

msg = (
    "MESH UPDATE from Agent-A: Agent-C (Gemini CLI v0.40.1) has joined the mesh at hwnd=0x2602034. "
    "Mesh is now: A (Claude/orchestrator) + B (you, GitHub task) + C (Gemini). "
    "Continue your GitHub CI investigation and write findings to Owner's Inbox/agent-b-github-report.md. "
    "When done, confirm AGENT-B TASK COMPLETE to A."
)

send_string(b_w, msg)
print("Agent-B updated")
