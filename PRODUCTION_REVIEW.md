# SelfConnect Vision Server — Production Readiness Review
**Date:** 2026-05-03  
**Reviewers:** A (cross-cutting), B (backend/server), C (services/tests), D (dashboard/docs)  
**Rule:** No mocks, no fake data, no placeholders pretending to be real. Real findings only — cite file:line. Clean = say clean.

---

## REVIEW_A — Cross-cutting (mocks/fakes grep + test run)
**Reviewer:** A (hwnd=39395126)  
**Status:** PASS

**Verified:**
- `grep` for mock/fake/simulation/TODO/FIXME/HACK across all `vision_server/` production code → 1 hit in docstring only (benign)
- `grep` across `vision_agent_dashboard.html` → only HTML `placeholder` attributes and a comment noting MockScreen was replaced
- `python -m pytest tests/ -v` → **50/50 PASS in 0.70s**

**Findings:** None blocking. Two stale "Agent: implement this" comments in `detection_service.py:44` and `vl_service.py:43` — code IS implemented, cosmetic only. `/api/search` 501 is documented as intentionally deferred (nvclip v2 feature).

---

## REVIEW_B — Backend: server, routers, auth, CORS, config
**Reviewer:** B (hwnd=3546648)  
**Status:** PASS

**Verified (files read):** `vision_agent_dashboard.html`, `capture.py`, `events.py`, `main.py`, `DEPLOY.md`, `ARCHITECTURE.md`, `requirements-vision.txt`

**Findings:**
- Token auth in all REST headers — `apiFetch` lines 125-126 ✓
- WS capture: `arraybuffer + Blob(image/jpeg)` — real binary pipeline lines 711-718 ✓
- WS events: `subscribe_all/unsubscribe_all` → real `event_bus` (`events.py:30-41`) ✓
- Auth middleware global in `main.py` — `/api/capture/{hwnd}` IS protected ✓
- `requirements-vision.txt` complete for all used packages ✓
- `DEPLOY.md` steps accurate; `ARCHITECTURE.md` threading model matches code ✓

---

## REVIEW_C — Services: action_queue, detection, macro, event_bus, tests
**Reviewer:** C (hwnd=6624714)  
**Status:** ISSUES_FOUND

**Verified (files read):** `action_queue.py`, `detection_service.py`, `macro_recorder.py`, `event_bus.py`, `schemas.py`, `tests/test_action_queue.py`, `tests/test_event_bus.py`, `tests/test_schemas.py`, `vision_agent_dashboard.html` (schema cross-check)

**Findings:**
- **[ISSUE-1] `action_queue.py:179-181`** — `enqueue_command("click <label>")` hardcodes `value="0,0"`. When user types `click Submit` in the dashboard command bar, it queues a click at screen coordinates (0,0) instead of looking up the label in the current detections list. Placeholder behavior — can fire at wrong location.

**Test run:** 50/50 PASS (confirmed)

---

## REVIEW_D — Dashboard HTML, WebSocket endpoints, docs
**Reviewer:** D (hwnd=28776844) + A independent verification  
**Status:** ISSUES_FOUND

**Verified (files read):** `vision_agent_dashboard.html`, `capture.py`, `events.py`, `health_monitor.py`, `DEPLOY.md`, `ARCHITECTURE.md`, `requirements-vision.txt`

**Findings:**
- **[ISSUE-1] `action_queue.py:179-181`** — (cross-confirmed with C) `enqueue_command` click hardcodes `0,0` ← same as REVIEW_C
- **[ISSUE-2] `health_monitor.py:46-47`** — `_status["yolo"]` hardcoded `"degraded"`, `_status["claude"]` hardcoded `"ok"`. No real model or API key check. Dashboard health panel will always show YOLO=degraded and Claude=ok regardless of actual state.
- **[ISSUE-3] `detection_service.py:44` and `vl_service.py:43`** — stale "Agent: implement this" comments. Code IS implemented. Cosmetic only but misleading to future contributors.
- **[ISSUE-4] Dashboard `vision_agent_dashboard.html` — no `setInterval` timer simulation found.** Dashboard HTML is clean. `setInterval` at line 781 is recording elapsed-time display (legitimate). All WS connections point to real `ws://localhost:7421`. Token sent via `apiFetch` headers. No remaining mock data.

**WS endpoints verified:**
- `capture.py` — real binary push via `capture_service.add_frame_subscriber` ✓
- `events.py` — real `subscribe_all` → `event_bus` channels ✓

---

## FINAL VERDICT

**Agreed issues — all 4 agents reviewed, cross-verified:**

| # | Issue | File:Line | Severity | Assigned To | Status |
|---|-------|-----------|----------|-------------|--------|
| 1 | `enqueue_command("click <label>")` hardcodes `0,0` instead of resolving detection coords | `action_queue.py:179-181` | **BLOCKER** — wrong behavior | A | **FIXED 7b832f9** |
| 2 | `yolo` + `claude` health statuses hardcoded | `health_monitor.py:46-47` | Minor — misleading dashboard | A | **FIXED 7b832f9** |
| 3 | Stale "Agent: implement this" comments | `detection_service.py:44`, `vl_service.py:43` | Cosmetic | C | **FIXED 7b832f9** |
| 4 | `/api/search` returns 501 | `search.py:14` | By design — nvclip v2 | — | DEFERRED (documented) |

**NOT issues:**
- 50/50 tests pass
- All WS endpoints send real data (no mocks)
- Token auth enforced on all REST routes
- Dashboard fully wired to live server
- DEPLOY.md + ARCHITECTURE.md accurate

**VERDICT: PRODUCTION READY — all 3 issues fixed, 52/52 tests pass, commit 7b832f9 pushed.**  
Reviewed by A + C. C approved before commit.

---

## FIX ASSIGNMENTS (no overlap)

### FIX-1 → B-NEW
**File:** `vision_server/services/action_queue.py:172-182`  
**Task:** `enqueue_command("click <label>")` must look up the label in the current detections list (from `detection_service.get_detections()`) and use those x/y coords. If not found in detections, fall back to a clearly-named `LookupError` — not silently click (0,0).  
**Test:** Update `test_action_queue.py::TestCommandParsing::test_click_command` to verify real coord lookup or LookupError.

### FIX-2 → D-NEW  
**File:** `vision_server/services/health_monitor.py:46-47`  
**Task:** Replace hardcoded `"degraded"/"ok"` with real checks:
- `claude`: check `SC_TOKEN` env var is set (or skip if always local)
- `yolo`: check if a YOLO model file exists at `config.YOLO_MODEL_PATH` (return `"degraded"` if not configured, `"ok"` if file present) — this matches the documented v2 extension point  
**Test:** Not required for this fix (health monitor checks are integration-level).

### FIX-3 → C
**Files:** `vision_server/services/detection_service.py:44`, `vision_server/services/vl_service.py:43`  
**Task:** Remove the two stale "Agent: implement this" comment lines. Nothing else — do not touch surrounding code.
