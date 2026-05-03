"""
config.py — Settings for the SelfConnect Vision Server.
All tuneable values live here.
"""
import os
import secrets

# Server
HOST = "127.0.0.1"   # localhost only — security requirement
PORT = 7421

# Auth — random token generated at startup, printed to terminal
# Can be overridden via SC_TOKEN env var for scripted clients
SESSION_TOKEN: str = os.environ.get("SC_TOKEN") or secrets.token_urlsafe(24)

# Capture
CAPTURE_FPS = 15           # target; actual depends on window size
CAPTURE_QUALITY = 75       # JPEG quality (0-100)
CAPTURE_MIN_NONZERO = 0.01 # fraction of non-zero pixels; below = all-black, use fallback

# HWND of active capture target (updated via /api/windows/{hwnd}/attach)
active_hwnd: int = 0

# Ollama
OLLAMA_URL = "http://localhost:11434"
OLLAMA_VL_MODEL = os.environ.get("SC_VL_MODEL", "llava:7b")
OLLAMA_VL_TIMEOUT = 300    # seconds (llava cold-start on first load can take 120-180s)

# Detection
DETECTION_WIN32_CONFIDENCE = 0.90  # Win32 controls are exact, high confidence
DETECTION_VL_CONFIDENCE = 0.70     # llava-derived positions are approximate

# Action queue
ACTION_FOCUS_VERIFY = True   # verify window focus before each send_string
ACTION_STEP_DELAY = 0.05     # seconds between queue steps

# Macro recorder
MACRO_EXPORT_DIR = "macros"

# Paths
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK_PATH = BASE_DIR
