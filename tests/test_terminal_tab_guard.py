from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest

import sc_guarded_submit as guarded
import sc_mesh_registry
import sc_terminal_tab as tabguard
import self_connect


class _Array:
    def __init__(self, values):
        self.values = list(values)
        self.Length = len(self.values)

    def GetElement(self, index):
        return self.values[index]


class _Selection:
    def __init__(self, element, root):
        self.element = element
        self.root = root

    @property
    def CurrentIsSelected(self):
        return int(self.element.selected)

    def Select(self):
        for item in self.root.tabs:
            item.selected = False
        for term in self.root.terms:
            term.focused = False
        self.element.selected = True
        self.root.term = self.element.term
        self.element.term.focused = True
        self.root.focused_element = self.element.term

    def QueryInterface(self, _interface):
        return self


class _TextRange:
    def __init__(self, text):
        self.text = text

    def GetText(self, _maximum):
        return self.text


class _TextPattern:
    def __init__(self, text):
        self.DocumentRange = _TextRange(text)

    def QueryInterface(self, _interface):
        return self


class _Element:
    def __init__(self, rid, *, selected=False, text=None, token=None, focused=False):
        self.rid = tuple(rid)
        self.selected = selected
        self.text = text
        self.token = token or object()
        self.focused = focused
        self.term = None
        self.root = None
        self.parent = None

    def GetRuntimeId(self):
        return self.rid

    def GetCurrentPropertyValue(self, property_id):
        if property_id == 30079:
            return self.selected
        raise AssertionError(property_id)

    def GetCurrentPattern(self, pattern_id):
        if pattern_id == 10010:
            return _Selection(self, self.root)
        if pattern_id == 10014 and self.text is not None:
            return _TextPattern(self.text)
        raise RuntimeError("pattern unavailable")

    @property
    def CurrentHasKeyboardFocus(self):
        return self.focused


class _Root:
    def __init__(self, hwnd, pid, tabs):
        self.hwnd = hwnd
        self.pid = pid
        self.tabs = list(tabs)
        for item in self.tabs:
            item.root = self
        self.terms = [item.term for item in self.tabs]
        self.term = next(item.term for item in self.tabs if item.selected)
        self.focused_element = self.term

    def GetCurrentPropertyValue(self, property_id):
        return {30020: self.hwnd, 30002: self.pid}[property_id]

    def FindAll(self, _scope, condition):
        property_id, value = condition
        if (property_id, value) == (30003, 50019):
            return _Array(self.tabs)
        if (property_id, value) == (30040, True):
            return _Array(self.terms)
        raise AssertionError(condition)


class _Uia:
    def __init__(self, root):
        self.root = root

    def ElementFromHandle(self, hwnd):
        if hwnd != self.root.hwnd:
            raise RuntimeError("missing")
        return self.root

    def CreatePropertyCondition(self, property_id, value):
        return property_id, value

    def CompareElements(self, left, right):
        return left.token is right.token

    def GetFocusedElement(self):
        return self.root.focused_element

    @property
    def ControlViewWalker(self):
        return _Walker()


class _Walker:
    def GetParentElement(self, element):
        return element.parent


class _Module:
    IUIAutomationSelectionItemPattern = object()
    IUIAutomationTextPattern = object()


def _fixture():
    term_a = _Element((42, 10, 4, 101), text="A buffer", focused=True)
    term_b = _Element((42, 10, 4, 202), text="B buffer")
    tab_a = _Element((42, 10, 4, 11), selected=True)
    tab_b = _Element((42, 10, 4, 22), selected=False)
    tab_a.term = term_a
    tab_b.term = term_b
    root = _Root(1001, 2002, [tab_a, tab_b])
    return _Uia(root), _Module(), root, tab_a, tab_b


def _target():
    return guarded.TargetIdentity(
        hwnd=1001,
        pid=2002,
        exe_name="WindowsTerminal.exe",
        class_name="CASCADIA_HOSTING_WINDOW_CLASS",
        title="duplicate title",
        exe_path=r"C:\WindowsApps\WindowsTerminal.exe",
        process_start_time_ns=3003,
    )


def test_capture_uses_selected_element_not_duplicate_title_or_index(monkeypatch):
    uia, module, root, tab_a, tab_b = _fixture()
    monkeypatch.setattr(tabguard, "_get_uia", lambda: (uia, module))
    guard = tabguard.capture_active_terminal_tab(_target(), peer_birth_id="peer-a-1234")
    assert guard.identity.tab_runtime_id == tab_a.rid
    root.tabs[:] = [tab_b, tab_a]
    result = guard.checkpoint("after-reorder", select=False, deadline=time.monotonic() + 1)
    assert result["retained_compare"] is True
    assert result["runtime_id_scope"] == "desktop-session-opaque-reusable"
    assert result["exclusive_routing_claimed"] is False


def test_selects_retained_tab_and_requires_matching_term_control(monkeypatch):
    uia, module, root, tab_a, tab_b = _fixture()
    monkeypatch.setattr(tabguard, "_get_uia", lambda: (uia, module))
    guard = tabguard.capture_active_terminal_tab(_target(), peer_birth_id="peer-a-1234")
    _Selection(tab_b, root).Select()
    with pytest.raises(tabguard.TerminalTabGuardError, match="sole selected"):
        guard.checkpoint("wrong-tab", select=False, deadline=time.monotonic() + 1)
    assert guard.checkpoint("reselect", select=True, deadline=time.monotonic() + 1)["selected"] is True
    root.term.focused = False
    wrong = _Element((42, 10, 4, 999), text="wrong pane", focused=True)
    root.terms.append(wrong)
    root.focused_element = wrong
    with pytest.raises(tabguard.TerminalTabGuardError, match="TermControl"):
        guard.checkpoint("wrong-term", select=False, deadline=time.monotonic() + 1)


def test_active_term_control_denies_zero_focused_text_candidates():
    uia, module, root, _tab_a, _tab_b = _fixture()
    root.focused_element = _Element((42, 99), text=None)
    with pytest.raises(tabguard.TerminalTabGuardError, match="exactly one focused"):
        tabguard._active_term_control(uia, module, root)


def test_active_term_control_denies_multiple_focused_text_candidates():
    uia, module, root, _tab_a, _tab_b = _fixture()
    root.terms[1].parent = root.terms[0]
    root.focused_element = root.terms[1]
    with pytest.raises(tabguard.TerminalTabGuardError, match="exactly one focused"):
        tabguard._active_term_control(uia, module, root)


def test_active_term_control_ignores_longer_inactive_text_candidate():
    uia, module, root, tab_a, _tab_b = _fixture()
    root.terms[1].text = "inactive" * 1000
    assert tabguard._active_term_control(uia, module, root) is tab_a.term


def test_closed_reopened_same_runtime_id_is_stale_by_compare_elements(monkeypatch):
    uia, module, root, tab_a, _tab_b = _fixture()
    monkeypatch.setattr(tabguard, "_get_uia", lambda: (uia, module))
    guard = tabguard.capture_active_terminal_tab(_target(), peer_birth_id="peer-a-1234")
    replacement = _Element(tab_a.rid, selected=True)
    replacement.term = tab_a.term
    replacement.root = root
    root.tabs[0] = replacement
    with pytest.raises(tabguard.TerminalTabGuardError, match="stale or ambiguous"):
        guard.checkpoint("reused-runtime", select=False, deadline=time.monotonic() + 1)


def test_operation_snapshot_exact_binds_terminal_tab_identity():
    identity = tabguard.TerminalTabIdentity(
        window_hwnd=1001,
        window_pid=2002,
        window_process_start_time_ns=3003,
        tab_runtime_id=(42, 10, 4, 11),
        term_control_runtime_id=(42, 10, 4, 101),
        peer_birth_id="peer-a-1234",
    )
    operation = {
        "message_id": "11" * 16,
        "challenge": "22" * 32,
        "key_id": "key-a",
        "response_key_id": "key-b",
        "input_sha256": hashlib.sha256(b"input").hexdigest(),
        "input_bytes": 5,
        "sender": "sender-a",
        "receiver": "receiver-b",
        "target": asdict(_target()),
        "terminal_tab": asdict(identity),
    }
    snapshot = guarded._operation_snapshot_bytes(operation)
    decoded = json.loads(snapshot)
    assert decoded["terminal_tab"]["peer_birth_id"] == "peer-a-1234"
    baseline = guarded._stable_operation_sha256(snapshot)
    decoded["terminal_tab"]["term_control_runtime_id"][-1] += 1
    changed = guarded._stable_operation_sha256(guarded._operation_snapshot_bytes(decoded))
    assert changed != baseline
    decoded["terminal_tab"]["extra"] = True
    with pytest.raises(guarded.AckReplayError, match="terminal-tab identity schema"):
        guarded._operation_snapshot_bytes(decoded)


def test_terminal_tab_identity_must_bind_top_level_target():
    identity = tabguard.TerminalTabIdentity(
        window_hwnd=9999,
        window_pid=2002,
        window_process_start_time_ns=3003,
        tab_runtime_id=(1,),
        term_control_runtime_id=(2,),
        peer_birth_id="peer-a",
    )
    operation = {
        "message_id": "11" * 16,
        "challenge": "22" * 32,
        "key_id": "key-a",
        "response_key_id": "key-b",
        "input_sha256": "33" * 32,
        "input_bytes": 1,
        "sender": "sender-a",
        "receiver": "receiver-b",
        "target": asdict(_target()),
        "terminal_tab": asdict(identity),
    }
    with pytest.raises(guarded.AckReplayError, match="top-level target binding"):
        guarded._operation_snapshot_bytes(operation)


class _OperationGuard:
    def __init__(self, identity, fail_stage=None):
        self.identity = identity
        self.fail_stage = fail_stage
        self.calls = []

    def checkpoint(self, stage, *, select, deadline):
        assert deadline > time.monotonic()
        self.calls.append((stage, select))
        if stage == self.fail_stage:
            raise tabguard.TerminalTabGuardError(stage)
        return {"ok": True, "stage": stage, "selected": True}


def _submit_with_tab(
    tmp_path,
    *,
    fail_stage=None,
    raise_after_side_effect=False,
    malformed_result=False,
    zero_accepted=False,
    zero_mutation=None,
    returned_result_factory=None,
):
    target = _target()
    identity = tabguard.TerminalTabIdentity(
        window_hwnd=target.hwnd,
        window_pid=target.pid,
        window_process_start_time_ns=target.process_start_time_ns,
        tab_runtime_id=(42, 10, 4, 11),
        term_control_runtime_id=(42, 10, 4, 101),
        peer_birth_id="peer-a-1234",
    )
    tab = _OperationGuard(identity, fail_stage)
    key_id = "key-a"
    keyring = guarded.AckKeyRing({key_id: b"k" * 32})
    sent = []

    def send_body(_window, text, _transport, _deadline):
        sent.append(text)
        if returned_result_factory is not None:
            return returned_result_factory()
        if raise_after_side_effect:
            raise OSError("transport outcome unknown after native call")
        if malformed_result:
            return None
        if zero_accepted:
            result = {
                "ok": False,
                "transport": "postmessage_wm_char",
                "target_hwnd": target.hwnd,
                "target_pid": target.pid,
                "chars_requested": 1,
                "chars_accepted": 0,
                "delivery_evidence": "message_queue_acceptance_only",
                "delivery_verified": False,
                "error": "postmessage_queue_rejected",
                "winerror": 5,
                "delivery_hwnd": target.hwnd + 1,
            }
            if zero_mutation is not None:
                zero_mutation(result)
            return result
        return {
            "ok": True,
            "transport": "postmessage_wm_char",
            "chars_requested": len(text),
            "chars_accepted": len(text),
            "delivery_verified": False,
        }

    def receive_ack(request, _timeout):
        return guarded.sign_peer_ack(
            keyring=keyring,
            key_id=key_id,
            message_id=request.message_id,
            challenge=request.challenge,
            attempt_nonce=request.attempt_nonce,
            ack_nonce="44" * 32,
            input_sha256=request.input_sha256,
            operation_sha256=request.operation_sha256,
            sender=request.receiver,
            receiver=request.sender,
            decision="accepted",
        )

    tokens = iter(("11" * 16, "22" * 32, "33" * 32))
    authorities = guarded._InjectedTestAuthorities(
        snapshot=lambda _hwnd: (SimpleNamespace(**asdict(target)), target),
        send_body=send_body,
        focus=lambda hwnd, _deadline: {"ok": True, "hwnd": hwnd},
        enter=lambda expected, _deadline: {"ok": True, "hwnd": expected.hwnd, "events_inserted": 2},
        receive_ack=receive_ack,
        finalize_ack=guarded.DurableAckFinalizer(tmp_path / "finalizer.sqlite3").finalize,
        audit_append=sc_mesh_registry.append_event,
        token_hex=lambda count: next(tokens),
    )
    result = guarded._guarded_submit_impl(
        "abc",
        target=target,
        sender="sender-a",
        receiver="receiver-b",
        keyring=keyring,
        key_id=key_id,
        event_log_path=tmp_path / "events.jsonl",
        authorities=authorities,
        transport="auto",
        ack_timeout=2.0,
        max_ack_age_seconds=300.0,
        terminal_tab_guard=tab,
    )
    return result, tab, sent


def test_guarded_submit_checks_active_tab_around_every_native_batch_and_binds_ack(tmp_path):
    result, tab, sent = _submit_with_tab(tmp_path)
    assert result["state"] == "acknowledged"
    assert sent == ["a", "b", "c"]
    assert result["terminal_tab"]["peer_birth_id"] == "peer-a-1234"
    assert result["ack"]["operation_sha256"]
    assert tab.calls == [
        ("before_body", True),
        ("before_body_batch_0", False),
        ("after_body_batch_0", False),
        ("before_body_batch_1", False),
        ("after_body_batch_1", False),
        ("before_body_batch_2", False),
        ("after_body_batch_2", False),
        ("immediately_before_hardware_enter", False),
        ("immediately_after_hardware_enter", False),
    ]


def test_pre_body_tab_drift_refuses_before_native_input(tmp_path):
    result, _tab, sent = _submit_with_tab(tmp_path, fail_stage="before_body")
    assert result["state"] == "refused"
    assert result["error"].startswith("terminal_tab_guard_failed_before_body")
    assert sent == []


def test_post_body_batch_tab_drift_is_ambiguous_and_stops(tmp_path):
    result, _tab, sent = _submit_with_tab(tmp_path, fail_stage="after_body_batch_0")
    assert result["state"] == "ambiguous"
    assert result["error"].startswith("terminal_tab_or_body_batch_failed")
    assert result["chars_accepted"] == 1
    assert sent == ["a"]


def test_batch_zero_exception_after_native_side_effect_is_ambiguous(tmp_path):
    result, _tab, sent = _submit_with_tab(tmp_path, raise_after_side_effect=True)
    assert result["state"] == "ambiguous"
    assert result["error"].startswith("terminal_tab_or_body_batch_failed")
    assert result["chars_accepted"] == 0
    assert sent == ["a"]


def test_batch_zero_malformed_result_after_native_entry_is_ambiguous(tmp_path):
    result, _tab, sent = _submit_with_tab(tmp_path, malformed_result=True)
    assert result["state"] == "ambiguous"
    assert result["chars_accepted"] == 0
    assert sent == ["a"]


def test_batch_zero_explicit_zero_acceptance_is_refused(tmp_path):
    result, _tab, sent = _submit_with_tab(tmp_path, zero_accepted=True)
    assert result["state"] == "refused"
    assert result["error"] == "body_transport_zero_accepted"
    assert sent == ["a"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: result.__setitem__("ok", True),
        lambda result: result.__setitem__("chars_requested", 2),
        lambda result: result.update({"ok": True, "chars_requested": 2}),
        lambda result: result.__setitem__("chars_accepted", False),
        lambda result: result.__setitem__("target_hwnd", 9999),
        lambda result: result.__setitem__("extra", "unmodeled"),
        lambda result: result.pop("error"),
    ],
)
def test_malformed_or_inconsistent_zero_result_is_ambiguous(tmp_path, mutation):
    result, _tab, sent = _submit_with_tab(
        tmp_path,
        zero_accepted=True,
        zero_mutation=mutation,
    )
    assert result["state"] == "ambiguous"
    assert result["error"] == "body_staged_partial_or_unknown"
    assert sent == ["a"]


def test_real_production_postmessage_zero_rejection_is_refused(
    tmp_path,
    monkeypatch,
):
    target = _target()
    window = SimpleNamespace(**asdict(target))
    monkeypatch.setattr(self_connect, "find_child_by_class", lambda *_args: target.hwnd + 1)
    monkeypatch.setattr(self_connect, "_send_char_postmessage", lambda *_args: False)
    production_result = guarded._production_send_body(
        window,
        "a",
        "postmessage",
        time.monotonic() + 1,
    )
    assert guarded._is_explicit_zero_postmessage_rejection(production_result, window)

    result, _tab, sent = _submit_with_tab(
        tmp_path,
        returned_result_factory=lambda: dict(production_result),
    )
    assert result["state"] == "refused"
    assert result["error"] == "body_transport_zero_accepted"
    assert sent == ["a"]


@pytest.mark.parametrize(
    "stage",
    ["immediately_before_hardware_enter", "immediately_after_hardware_enter"],
)
def test_enter_boundary_tab_drift_is_ambiguous(tmp_path, stage):
    result, _tab, sent = _submit_with_tab(tmp_path, fail_stage=stage)
    assert result["state"] == "ambiguous"
    assert "terminal_tab_guard_failed" in result["error"]
    assert sent == ["a", "b", "c"]
