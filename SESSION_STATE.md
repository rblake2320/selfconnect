# SelfConnect Mesh — Session State
**Last updated:** 2026-05-03 (session 12)
**Status:** PAUSED — system reboot

## WARNING: HWNDs change on every reboot
All HWNDs below are from this session only. On next boot, re-discover them using:
```python
python -c "
import sys, os; os.environ['PYTHONIOENCODING']='utf-8'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from self_connect import list_windows
for w in list_windows():
    t = w.title.encode('ascii','replace').decode('ascii')
    print(f'0x{w.hwnd:x}: {t[:70]}')
"
```

## Session 12 Agent HWNDs (STALE after reboot)
| Agent | Model | HWND | Window Title |
|-------|-------|------|-------------|
| A (orchestrator) | Claude Sonnet 4.6 | 0x17b1322 | airgap-sop-production |
| B | Claude Code | 0x1311316 | SelfConnect mesh peer terminal setup |
| C | Gemini CLI v0.40.1 | 0x2602034 | Action Required (techai) |
| D | Codex v0.125.0 (GPT-5) | 0x1870dac | techai / airgap-sop |

## SelfConnect Pipeline Status (confirmed operational — session 12)

```
hub_relay.py running in Windows Session 1 (interactive, can see all GUI windows)
MESH_STATUS: all 5 named nodes + extras ONLINE
CMD: relay works end-to-end with TAG correlation (45s timeout, 2s poll)
Direct chat with Session A via SSH bridge: inject text → read response
```

### Operational Rules (must follow on every restart)
| Rule | Why |
|------|-----|
| hub_relay.py MUST run in Windows Session 1 | Session 0 (services) cannot see GUI windows — Antigravity injection fails |
| Use Task Scheduler HubRelay task (`/it /rl highest`) | Ensures Session 1 interactive context |
| Stop `selfconnect-peer.service` before running `spark2_client.py` | They compete for the same Hub inbox |
| spark2_client HUB = `http://10.0.0.1:8765` | Spark1 QSFP address, reachable from Spark-2 |

### Node B Status (last known)
- IDLE at `ai-to-ai-bidirectional-chat` terminal
- Ran migration test autonomously during A's context compaction
- Checkpoint `sc_test_mc2.json` written, successor spawned at hwnd=22610408, both PASS
- Last line: "What do you want to do next?"
- Session A can relay instructions to B via `send_frame`

### Spark-2 → Windows Model Access (NEW — session 12)

Spark-2 injects into Agent-A's terminal via hub_relay → that terminal can run
`antigravity_controller.py` → reaches any model in Antigravity on the Windows desktop.

```bash
# From Spark-2 — send a message to Gemini 3.1 Pro on Windows:
python spark2_client.py --cmd "python antigravity_controller.py --chat 'Hello from Spark-2'"

# Query current model:
python spark2_client.py --cmd "python antigravity_controller.py --model"
```

**Models available on Windows via Antigravity:** Gemini 3.1 Pro (default), Claude Sonnet 4.6, Claude Opus 4.6, GPT-OSS-120b

---

## What was accomplished this session
- ✅ 4-agent mesh live: A (Claude) + B (Claude) + C (Gemini) + D (Codex)
- ✅ selfconnect CI: fixed all ruff errors, CI GREEN
- ✅ airgap-sop CI: Agent-B fixed all 22 ruff errors, CI GREEN (commit 1a5261a)
- ✅ pka-workspace CI: fixed all ruff errors, CI GREEN
- ✅ Bidirectional mesh: agents can report back to A via send_string(A_HWND)
- ✅ All mesh scripts committed and pushed to GitHub
- ⚠️ vidintel: needs VERCEL_TOKEN secret added in GitHub Settings (user action)
- ⏳ TASK_REGISTRY.md: Agent-B was building this when paused

## To restart the mesh after reboot
1. Open a new Claude Code terminal (this becomes Agent-A)
2. Open a new terminal → run `claude` → this becomes Agent-B
3. Open a new terminal → run `gemini` → this becomes Agent-C  
4. Open a new terminal → run `node %APPDATA%\npm\node_modules\@openai\codex\bin\codex.js` → Agent-D
5. Run `python -c "from self_connect import list_windows; ..."` to find new HWNDs
6. Update mesh_config.py with new HWNDs
7. Run `python add_return_protocol.py` to re-establish bidirectional comms
8. Resume TASK_REGISTRY.md task on Agent-B
