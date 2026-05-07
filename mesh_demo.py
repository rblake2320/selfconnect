import requests
import json
import os
import sys
from datetime import datetime

# Configuration
OLLAMA_API_URL = "http://localhost:11434/api/chat"
AGENT_B_MODEL = "qwen3.6:27b"
INBOX_DIR = r"C:\Users\techai\PKA testing\Owner's Inbox"

def agent_a_assign_task(task_description: str) -> str:
    """Agent A (Orchestrator): Assigns task to Agent B via Ollama API"""
    print("[AGENT-A] Orchestrator: Initializing task assignment...")

    framed_prompt = (
        f"[AGENT-B] EXECUTE TASK: {task_description}\n"
        f"Provide a concise, structured response. Maintain technical accuracy.\n"
        f"Terminate your response exactly with [AGENT-B-COMPLETE]."
    )

    payload = {
        "model": AGENT_B_MODEL,
        "messages": [{"role": "user", "content": framed_prompt}],
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        agent_b_result = data.get("message", {}).get("content", "Execution timeout or empty response.")
        print("[AGENT-A] Task dispatched to Agent B. Awaiting execution...")
        return agent_b_result
    except requests.exceptions.RequestException as e:
        return f"[AGENT-A] ERROR: Ollama API unreachable or failed. Details: {e}"


def verify_delivery_stub(payload: str) -> bool:
    """Simulates Win32 SelfConnect verify_delivery session check"""
    if not payload:
        return False
    has_start = "[AGENT-B]" in payload
    has_end = "[AGENT-B-COMPLETE]" in payload
    return has_start and has_end


def save_capture_stub(payload: str, verified: bool) -> str:
    """Simulates Win32 SelfConnect save_capture session handler"""
    os.makedirs(INBOX_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"mesh_capture_{timestamp}.txt"
    filepath = os.path.join(INBOX_DIR, filename)

    capture_data = (
        f"[SELF-CONNECT SESSION] Delivery Verified: {verified}\n"
        f"[SELF-CONNECT SESSION] Timestamp: {timestamp}\n"
        f"[SELF-CONNECT SESSION] Source Agent: {AGENT_B_MODEL}\n"
        f"{'='*40}\n"
        f"{payload}\n"
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(capture_data)
    return filepath


def main():
    """Full demo cycle: A assigns -> B executes -> verify -> save to inbox"""
    print("="*40)
    print("[AGENT-A] Starting SelfConnect AI Mesh Demo Cycle")
    print("="*40)

    # 1. Agent A assigns task via Ollama API
    task = "Explain how decentralized AI agent meshes improve fault tolerance and latency compared to centralized orchestrators."
    agent_b_result = agent_a_assign_task(task)

    # 2. Agent B executes task (handled remotely by Ollama)
    print("\n[AGENT-B] EXECUTION OUTPUT:")
    print("-" * 40)
    print(agent_b_result)
    print("-" * 40)

    # 3. SelfConnect session reads and verifies the result
    print("\n[SELF-CONNECT SESSION] Reading result for verification...")
    is_verified = verify_delivery_stub(agent_b_result)
    print(f"[SELF-CONNECT SESSION] verify_delivery returned: {is_verified}")

    # 4. Save to inbox
    print("[SELF-CONNECT SESSION] Initiating save_capture stub...")
    if is_verified:
        saved_path = save_capture_stub(agent_b_result, True)
        print(f"[AGENT-A] Result successfully captured and saved to: {saved_path}")
    else:
        print("[AGENT-A] Verification failed. Framing mismatch. Skipping capture.")

    print("\n[AGENT-A] Mesh demo cycle complete.")


if __name__ == "__main__":
    main()
