"""
test_sc_send_disambiguation.py — mesh-registry disambiguation of duplicate
window titles in sc_send.

Background: tab names are project-scoped and reused across sessions, so two
visible windows titled "claude 2" can coexist (one live, one stale twin).
--first is a coinflip; the registry knows which hwnd carries the live
registration. Pure-function tests — no Win32, no registry file I/O.
"""
import os
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sc_send import pick_by_registry, registry_record_for  # noqa: E402

Win = namedtuple("Win", "hwnd title")


def reg(*agents):
    return {"agents": list(agents)}


def agent(hwnd, status="active", role="claude-2", birth="claude-2-abc123",
          last_seen=100.0):
    return {"hwnd": hwnd, "status": status, "role": role, "birth_id": birth,
            "last_seen": last_seen}


TWINS = [Win(0x1111, "claude 2"), Win(0x2222, "claude 2")]


def test_sole_active_registration_wins():
    winner, rows = pick_by_registry(
        TWINS, now=200.0, registry=reg(agent(0x2222), agent(0x1111, status="closing")))
    assert winner == TWINS[1]
    assert {(r[0].hwnd, r[1]) for r in rows} == {(0x1111, "closing"),
                                                 (0x2222, "active")}


def test_two_active_registrations_stay_ambiguous():
    winner, _ = pick_by_registry(
        TWINS, now=200.0, registry=reg(agent(0x1111, role="claude-2"),
                   agent(0x2222, role="claude-2-old")))
    assert winner is None


def test_no_active_registration_stays_ambiguous():
    winner, rows = pick_by_registry(
        TWINS, now=200.0, registry=reg(agent(0x1111, status="invalidated")))
    assert winner is None
    assert dict((r[0].hwnd, r[1]) for r in rows) == {0x1111: "invalidated",
                                                     0x2222: "unregistered"}


def test_rotated_hwnd_stays_ambiguous():
    # Active record points at an hwnd that no longer matches any visible
    # window — the twin windows themselves carry no active registration.
    winner, rows = pick_by_registry(TWINS, reg(agent(0x9999)))
    assert winner is None
    assert all(r[1] == "unregistered" for r in rows)


def test_unreadable_registry_stays_ambiguous():
    for registry in (None, {}, {"agents": []}):
        winner, rows = pick_by_registry(TWINS, registry)
        assert winner is None
        assert len(rows) == 2


def test_record_for_prefers_active_then_recency():
    stale = agent(0x1111, status="closing", birth="old", last_seen=50.0)
    live = agent(0x1111, status="active", birth="new", last_seen=10.0)
    assert registry_record_for(0x1111, reg(stale, live))["birth_id"] == "new"
    # No active record at all → most recently seen wins.
    older = agent(0x2222, status="closing", birth="older", last_seen=5.0)
    newer = agent(0x2222, status="standby", birth="newer", last_seen=90.0)
    assert registry_record_for(0x2222, reg(older, newer))["birth_id"] == "newer"
    assert registry_record_for(0x3333, reg(stale)) is None


def test_single_match_never_touches_registry_semantics():
    # One title match is not ambiguity; pick_by_registry is only consulted
    # for len(matches) > 1, but stays sane if called anyway.
    winner, rows = pick_by_registry([TWINS[0]], reg(agent(0x1111)), now=200.0)
    assert winner == TWINS[0]
    assert rows[0][1] == "active"


def test_stale_active_twin_cannot_win(sc_send_module=None):
    # F1 (claude-1 review): 31/39 active records were >7d old when measured.
    # An aged "active" record is not evidence the window is still that agent,
    # so it must not beat a live-unregistered twin.
    from sc_send import STALE_ACTIVE_SECONDS
    old = agent(0x1111, last_seen=100.0)
    now = 100.0 + STALE_ACTIVE_SECONDS + 1
    winner, rows = pick_by_registry(TWINS, reg(old), now=now)
    assert winner is None
    assert dict((r[0].hwnd, r[1]) for r in rows) == {0x1111: "active-stale",
                                                     0x2222: "unregistered"}


def test_fresh_active_beats_stale_active():
    from sc_send import STALE_ACTIVE_SECONDS
    now = 1000.0 + STALE_ACTIVE_SECONDS + 1
    stale = agent(0x1111, birth="stale-twin", last_seen=1000.0)
    fresh = agent(0x2222, birth="live", last_seen=now - 60)
    winner, rows = pick_by_registry(TWINS, reg(stale, fresh), now=now)
    assert winner == TWINS[1]
    assert dict((r[0].hwnd, r[1]) for r in rows) == {0x1111: "active-stale",
                                                     0x2222: "active"}
