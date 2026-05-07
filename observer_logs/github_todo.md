# GitHub Tracking — Observer Session 2026-05-07

## Pending Repo Updates

### Files Changed (Need Commit)
- `observer_briefing.md` — already in repo
- `mesh_config.py` — marked modified in git status
- New files (untracked, may need adding):
  - `b_reply.py`
  - `b_roundtrip.py`
  - `b_send.py`
  - `b_watcher.py`
  - `brief_b.py`
  - `briefing_b.txt`
  - `inject_b.py`
  - `local_agent.py`
  - `mesh_demo.py`
  - `read_b_and_relay.py`
  - `spawn_b.py`
  - `spawn_observer.py`

### Evidence to Capture
- [ ] Agent-B startup and initialization
- [ ] Cross-agent PostMessage communication (WM_CHAR protocol)
- [ ] Local model tool execution chain (Ollama → qwen3.6:27b)
- [ ] Hub relay connection (Windows-to-Spark-1)
- [ ] Context checkpoint and migration event
- [ ] Screenshots of patent-worthy moments

### Test Coverage
- [ ] Verify `list_windows()` works with current agent roster
- [ ] Verify `get_text_uia()` on all agent window types
- [ ] Verify `send_string()` PostMessage delivery to local_agent.py
- [ ] Verify `save_capture()` on Agent-B window
- [ ] Cross-platform test: Windows Terminal vs ConEmu vs WSL2

### Documentation
- [ ] Update README.md with observer role description
- [ ] Add event logging pattern to runbooks/
- [ ] Update CLAUDE.md with current HWND list (when stable)
- [ ] Create observer_logs/patent_evidence.md with proof references

### Tags to Create
- `observer-session-start` — mark when observer initialization complete
- `mesh-operational` — mark when all agents online
- `patent-claim-1-live` through `patent-claim-7-live` — as each claim gets evidence

### CI/CD
- [ ] Add observer_logs/ to .gitignore? (if logs should not be tracked)
- [ ] Or add observer_logs/*.md to git (if logs are legal evidence)?
- [ ] Decision: Legal evidence → commit. Debug output → .gitignore

---

## Session Goals

### Primary
- [x] Observer comes online and logs initial state
- [ ] Discover all agents (Agent-A, Agent-B, Agent-D, Agent-E)
- [ ] Capture 7 patent claim proofs
- [ ] Maintain structured event log

### Secondary
- [ ] Respond to Agent-A requests
- [ ] Escalate errors/failures from peers
- [ ] Track cross-machine relay activity
- [ ] Screenshot patent-worthy moments

---

## Notes for Next Session
- Agent-A (airgap-sop-production) not yet discovered — may not have started
- Agent-D (Codex) not yet discovered
- Agent-B is actively running — ready for observation
- Observer HWND and PID still need to be recorded once Terminal is stable
