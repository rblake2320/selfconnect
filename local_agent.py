"""
local_agent.py — Local Claude Code equivalent for SelfConnect mesh.
Uses Ollama HTTP API with native tool calling. Runs as interactive REPL.
"""
import sys, os, json, subprocess, re, requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
SC_DIR = os.path.dirname(os.path.abspath(__file__))

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = os.environ.get("AGENT_MODEL", "qwen3.6:27b")
CTX_WINDOW = int(os.environ.get("AGENT_CTX", "32768"))
MAX_ITER = 10
MAX_OUTPUT = 8000

# --- Bash denylist (destructive commands) ---
DENY_PATTERNS = [
    r'\brm\s+-rf\s+/', r'\bformat\b', r'\bmkfs\b', r'\bdd\s+if=',
    r'\b:>\s*/', r'\bdel\s+/[sq]', r'\brmdir\s+/s',
]

# === TOOL IMPLEMENTATIONS ===

def tool_bash_exec(command: str) -> str:
    for pat in DENY_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            return f"DENIED: command matches destructive pattern '{pat}'"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=30, cwd=SC_DIR)
        out = (r.stdout + r.stderr).strip()
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n...[truncated]"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s"
    except Exception as e:
        return f"ERROR: {e}"

def tool_file_read(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        if len(content) > MAX_OUTPUT:
            content = content[:MAX_OUTPUT] + "\n...[truncated]"
        return content or "(empty file)"
    except Exception as e:
        return f"ERROR: {e}"

def tool_file_write(path: str, content: str) -> str:
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"ERROR: {e}"

def tool_python_exec(code: str) -> str:
    try:
        r = subprocess.run([sys.executable, '-c', code], capture_output=True,
                           text=True, timeout=30, cwd=SC_DIR)
        out = (r.stdout + r.stderr).strip()
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n...[truncated]"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: timed out after 30s"
    except Exception as e:
        return f"ERROR: {e}"

# --- Phase 2: SelfConnect tools ---

def tool_list_windows() -> str:
    from self_connect import list_windows
    wins = list_windows()
    lines = []
    for w in wins:
        t = w.title.encode('ascii', 'replace').decode()
        lines.append(f"0x{w.hwnd:x}  {w.exe_name or '?':20s}  {t[:60]}")
    return '\n'.join(lines[:50]) or "(no windows)"

def tool_find_window(title_pattern: str) -> str:
    from self_connect import list_windows
    wins = list_windows()
    matches = [w for w in wins if title_pattern.lower() in w.title.lower()]
    if not matches:
        return f"No windows matching '{title_pattern}'"
    lines = [f"0x{w.hwnd:x}  {w.title.encode('ascii','replace').decode()[:60]}" for w in matches]
    return '\n'.join(lines)

def tool_read_window(hwnd: int) -> str:
    from self_connect import get_text_uia
    text = get_text_uia(hwnd)
    if not text:
        return f"No text from 0x{hwnd:x}"
    if len(text) > MAX_OUTPUT:
        text = text[-MAX_OUTPUT:]
    return text

def tool_send_message(hwnd: int, text: str) -> str:
    from self_connect import list_windows, send_string
    target = next((w for w in list_windows() if w.hwnd == hwnd), None)
    if not target:
        return f"Window 0x{hwnd:x} not found"
    # Normalize escaped \r and \n so the model's string literals become real control chars
    text = text.replace('\\r', '\r').replace('\\n', '\n')
    send_string(target, text, char_delay=0.02)
    return f"Sent {len(text)} chars to 0x{hwnd:x}"

# === TOOL REGISTRY ===

TOOL_DISPATCH = {
    "bash_exec": lambda **kw: tool_bash_exec(kw["command"]),
    "file_read": lambda **kw: tool_file_read(kw["path"]),
    "file_write": lambda **kw: tool_file_write(kw["path"], kw["content"]),
    "python_exec": lambda **kw: tool_python_exec(kw["code"]),
    "list_windows": lambda **kw: tool_list_windows(),
    "find_window": lambda **kw: tool_find_window(kw["title_pattern"]),
    "read_window": lambda **kw: tool_read_window(int(kw["hwnd"])),
    "send_message": lambda **kw: tool_send_message(int(kw["hwnd"]), kw["text"]),
}

TOOLS = [
    {"type": "function", "function": {
        "name": "bash_exec",
        "description": "Execute a shell command. Returns stdout+stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "Shell command to run"}
        }, "required": ["command"]}
    }},
    {"type": "function", "function": {
        "name": "file_read",
        "description": "Read a file and return its contents.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path"}
        }, "required": ["path"]}
    }},
    {"type": "function", "function": {
        "name": "file_write",
        "description": "Write content to a file. Creates directories if needed.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Content to write"}
        }, "required": ["path", "content"]}
    }},
    {"type": "function", "function": {
        "name": "python_exec",
        "description": "Execute a Python code snippet and return output.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Python code to execute"}
        }, "required": ["code"]}
    }},
    {"type": "function", "function": {
        "name": "list_windows",
        "description": "List all visible Windows desktop windows with HWND, exe, and title.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }},
    {"type": "function", "function": {
        "name": "find_window",
        "description": "Find windows whose title contains the given pattern.",
        "parameters": {"type": "object", "properties": {
            "title_pattern": {"type": "string", "description": "Substring to search for in window titles"}
        }, "required": ["title_pattern"]}
    }},
    {"type": "function", "function": {
        "name": "read_window",
        "description": "Read all text content from a window by HWND using UI Automation.",
        "parameters": {"type": "object", "properties": {
            "hwnd": {"type": "integer", "description": "Window handle (decimal integer)"}
        }, "required": ["hwnd"]}
    }},
    {"type": "function", "function": {
        "name": "send_message",
        "description": "Send text to another terminal window via SelfConnect PostMessage(WM_CHAR). Use this to communicate with other agents.",
        "parameters": {"type": "object", "properties": {
            "hwnd": {"type": "integer", "description": "Target window handle (decimal integer)"},
            "text": {"type": "string", "description": "Text to inject. Include \\r for Enter."}
        }, "required": ["hwnd", "text"]}
    }},
]

# === SYSTEM PROMPT ===

SYSTEM_PROMPT = f"""You are Agent-B, a local AI agent in the SelfConnect mesh.
You run on an RTX 5090 (32GB VRAM) with the {MODEL} model via Ollama.

You have tools to: execute bash commands, read/write files, run Python,
list desktop windows, read window text, and send messages to other agents.

Agent-A (Claude Code) is the orchestrator. To send a message to Agent-A,
use the send_message tool with Agent-A's HWND (you can find it with list_windows
or find_window). Include \\r at the end of messages to press Enter.

You are in: {SC_DIR}
SelfConnect SDK: {SC_DIR}/self_connect.py

Be concise. Execute tools to accomplish tasks. Don't explain what you'll do — just do it."""

# === AGENT LOOP ===

def execute_tool(name: str, arguments) -> str:
    # Handle arguments as dict or JSON string
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return f"ERROR: could not parse arguments: {arguments}"

    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return f"ERROR: unknown tool '{name}'"
    try:
        return fn(**arguments)
    except Exception as e:
        return f"ERROR executing {name}: {e}"


def strip_thinking(msg: dict) -> dict:
    """Remove thinking field from assistant messages."""
    if "thinking" in msg:
        msg = dict(msg)
        del msg["thinking"]
    return msg


def agent_loop(messages: list) -> str:
    seen_calls = set()
    for i in range(MAX_ITER):
        payload = {
            "model": MODEL,
            "messages": [strip_thinking(m) for m in messages],
            "tools": TOOLS,
            "stream": False,
            "options": {"num_ctx": CTX_WINDOW}
        }

        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return f"[Ollama error: {e}]"

        msg = data["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            content = msg.get("content", "")
            # Strip any <think>...</think> tags from content
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            return content

        for tc in tool_calls:
            fn = tc["function"]
            name = fn["name"]
            args = fn.get("arguments", {})

            # Dedup detection
            call_sig = f"{name}:{json.dumps(args, sort_keys=True)}"
            if call_sig in seen_calls:
                result = f"SKIPPED: duplicate call to {name} with same arguments"
            else:
                seen_calls.add(call_sig)
                print(f"  [{name}] {json.dumps(args)[:80]}")
                result = execute_tool(name, args)
                # Show brief result
                preview = result[:120].replace('\n', ' ')
                print(f"  -> {preview}{'...' if len(result) > 120 else ''}")

            messages.append({"role": "tool", "content": result})

    return "[max iterations reached]"


def prune_messages(messages: list, max_chars: int = 100000) -> list:
    """Drop oldest non-system messages when context gets too large."""
    total = sum(len(json.dumps(m)) for m in messages)
    while total > max_chars and len(messages) > 2:
        dropped = messages.pop(1)  # keep system prompt at [0]
        total -= len(json.dumps(dropped))
    return messages


# === MAIN REPL ===

def main():
    print(f"=== Local Agent — {MODEL} ===")
    print(f"Tools: {', '.join(TOOL_DISPATCH.keys())}")
    print(f"Type /quit to exit, /reset to clear context\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("agent-b> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input == "/quit":
            break
        if user_input == "/reset":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("Context cleared.")
            continue

        messages.append({"role": "user", "content": user_input})
        messages = prune_messages(messages)

        response = agent_loop(messages)
        print(f"\n{response}\n")


if __name__ == "__main__":
    main()
