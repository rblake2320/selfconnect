"""SelfConnect Mesh — agent HWNDs and return-to-A protocol."""

# Agent HWNDs (update when agents restart)
AGENT_A_HWND = 0x17b1322   # Claude Code orchestrator (airgap-sop-production tab)
AGENT_B_HWND = 0x1311316   # Claude Code peer (SelfConnect mesh peer terminal)
AGENT_C_HWND = 0x2602034   # Gemini CLI
AGENT_D_HWND = 0x1870dac   # Codex (techai tab)

MESH = {
    "A": AGENT_A_HWND,
    "B": AGENT_B_HWND,
    "C": AGENT_C_HWND,
    "D": AGENT_D_HWND,
}

# Standard task briefing footer — append to every task message
def return_protocol(agent_id: str) -> str:
    return (
        f" When your task is complete, send a completion message to Agent-A "
        f"at HWND {hex(AGENT_A_HWND)} using the SelfConnect SDK: "
        f"from self_connect import list_windows, send_string; "
        f"wins = list_windows(); "
        f"a = next((w for w in wins if w.hwnd == {hex(AGENT_A_HWND)}), None); "
        f"send_string(a, 'AGENT-{agent_id} TASK COMPLETE') if a else None"
    )
