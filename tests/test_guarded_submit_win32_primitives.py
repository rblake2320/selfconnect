from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

import self_connect as sc


def test_input_structure_matches_native_pointer_width():
    assert ctypes.sizeof(sc.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


class FakeUser32:
    def __init__(self, *, foreground=123, set_ok=True, bring_ok=True, inserted=2, retain_foreground=False):
        self.foreground = foreground
        self.set_ok = set_ok
        self.bring_ok = bring_ok
        self.inserted = inserted
        self.retain_foreground = retain_foreground

    def GetForegroundWindow(self):
        return self.foreground

    def GetWindowThreadProcessId(self, *_args):
        return 7

    def AttachThreadInput(self, *_args):
        return 1

    def ShowWindow(self, *_args):
        return 1

    def SetForegroundWindow(self, hwnd):
        if self.set_ok and not self.retain_foreground:
            self.foreground = hwnd
        return self.set_ok

    def BringWindowToTop(self, *_args):
        return self.bring_ok

    def SendInput(self, *_args):
        return self.inserted


@pytest.mark.parametrize(
    ("set_ok", "bring_ok", "observed", "expected_ok"),
    [(False, True, 123, False), (True, False, 123, False), (True, True, 123, True)],
)
def test_checked_focus_requires_api_acceptance_and_observed_foreground(monkeypatch, set_ok, bring_ok, observed, expected_ok):
    fake = FakeUser32(foreground=999, set_ok=set_ok, bring_ok=bring_ok)
    if set_ok:
        fake.foreground = 999
    monkeypatch.setattr(sc, "user32", fake)
    monkeypatch.setattr(sc, "kernel32", SimpleNamespace(GetCurrentThreadId=lambda: 7, GetLastError=lambda: 5))
    result = sc.focus_window_checked(observed, settle_seconds=0)
    assert result["ok"] is expected_ok


@pytest.mark.parametrize("inserted", [0, 1])
def test_hardware_enter_rejects_partial_sendinput(monkeypatch, inserted):
    monkeypatch.setattr(sc, "user32", FakeUser32(foreground=123, inserted=inserted))
    monkeypatch.setattr(sc, "kernel32", SimpleNamespace(GetLastError=lambda: 5))
    result = sc.hardware_enter_checked(123)
    assert result["ok"] is False
    assert result["events_inserted"] == inserted


def test_checked_focus_rejects_success_return_without_foreground_observation(monkeypatch):
    fake = FakeUser32(foreground=999, set_ok=True, bring_ok=True, retain_foreground=True)
    monkeypatch.setattr(sc, "user32", fake)
    monkeypatch.setattr(sc, "kernel32", SimpleNamespace(GetCurrentThreadId=lambda: 7, GetLastError=lambda: 0))
    result = sc.focus_window_checked(123, settle_seconds=0)
    assert result["ok"] is False
    assert result["foreground_hwnd"] == 999


def test_checked_focus_accepts_already_foreground_without_set_call(monkeypatch):
    fake = FakeUser32(foreground=123, set_ok=False)
    fake.SetForegroundWindow = lambda *_args: pytest.fail("already-foreground target must not be refocused")
    monkeypatch.setattr(sc, "user32", fake)
    result = sc.focus_window_checked(123, settle_seconds=0)
    assert result["ok"] is True
    assert result["already_foreground"] is True


def test_hardware_enter_rejects_wrong_foreground_without_sendinput(monkeypatch):
    fake = FakeUser32(foreground=999)
    fake.SendInput = lambda *_args: pytest.fail("SendInput must not run for wrong foreground")
    monkeypatch.setattr(sc, "user32", fake)
    result = sc.hardware_enter_checked(123)
    assert result["ok"] is False
    assert result["error"] == "foreground_changed_before_enter"


def test_hardware_enter_accepts_exact_two_events(monkeypatch):
    monkeypatch.setattr(sc, "user32", FakeUser32(foreground=123, inserted=2))
    monkeypatch.setattr(sc, "kernel32", SimpleNamespace(GetLastError=lambda: 0))
    result = sc.hardware_enter_checked(123)
    assert result["ok"] is True
    assert result["events_inserted"] == 2


def test_hardware_enter_holds_process_handle_and_checks_full_identity_around_sendinput(monkeypatch):
    expected = {
        "hwnd": 123, "pid": 456, "exe_name": "peer.exe", "class_name": "ConsoleWindowClass",
        "title": "exact", "exe_path": r"C:\peer.exe", "process_start_time_ns": 99,
    }
    fake_user = FakeUser32(foreground=123, inserted=2)
    closed = []
    fake_kernel = SimpleNamespace(
        OpenProcess=lambda access, inherit, pid: 777 if access == 0x101000 and not inherit and pid == 456 else 0,
        CloseHandle=lambda handle: closed.append(handle) or 1,
        GetLastError=lambda: 0,
    )
    snapshots = []

    def identity(hwnd, handle):
        snapshots.append((hwnd, handle))
        return {**expected, "window_pid_matches_handle": True}

    monkeypatch.setattr(sc, "user32", fake_user)
    monkeypatch.setattr(sc, "kernel32", fake_kernel)
    monkeypatch.setattr(sc, "_enter_target_identity", identity)
    result = sc.hardware_enter_checked(123, expected_identity=expected)
    assert result["ok"] is True
    assert snapshots == [(123, 777), (123, 777)]
    assert closed == [777]
    assert "inside SendInput" in result["irreducible_race_bound"]


def test_hardware_enter_post_identity_change_is_ambiguous_after_two_events(monkeypatch):
    expected = {
        "hwnd": 123, "pid": 456, "exe_name": "peer.exe", "class_name": "ConsoleWindowClass",
        "title": "exact", "exe_path": r"C:\peer.exe", "process_start_time_ns": 99,
    }
    calls = 0

    def identity(_hwnd, _handle):
        nonlocal calls
        calls += 1
        value = dict(expected)
        if calls == 2:
            value["process_start_time_ns"] = 100
        return {**value, "window_pid_matches_handle": True}

    monkeypatch.setattr(sc, "user32", FakeUser32(foreground=123, inserted=2))
    monkeypatch.setattr(sc, "kernel32", SimpleNamespace(
        OpenProcess=lambda *_args: 777, CloseHandle=lambda *_args: 1, GetLastError=lambda: 0,
    ))
    monkeypatch.setattr(sc, "_enter_target_identity", identity)
    result = sc.hardware_enter_checked(123, expected_identity=expected)
    assert result["ok"] is False
    assert result["events_inserted"] == 2
    assert result["identity_stable"] is False
