"""Bounded Windows Terminal active-tab identity and selection guard.

UI Automation identifies the selected tab at an instant. It does not make
selection and later Win32 input one atomic operation. Callers must therefore
treat a failed pre-call checkpoint as refusal and a failed post-call checkpoint
as ambiguous: input may already have reached whichever tab was active.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any


_UIA_CONTROL_TYPE_PROPERTY_ID = 30003
_UIA_NATIVE_WINDOW_HANDLE_PROPERTY_ID = 30020
_UIA_PROCESS_ID_PROPERTY_ID = 30002
_UIA_IS_TEXT_PATTERN_AVAILABLE_PROPERTY_ID = 30040
_UIA_SELECTION_ITEM_IS_SELECTED_PROPERTY_ID = 30079
_UIA_TAB_ITEM_CONTROL_TYPE_ID = 50019
_UIA_TREE_SCOPE_SUBTREE = 4
_UIA_SELECTION_ITEM_PATTERN_ID = 10010
_UIA_TEXT_PATTERN_ID = 10014
_BIRTH_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
RUNTIME_ID_SCOPE = "desktop-session-opaque-reusable"


class TerminalTabGuardError(RuntimeError):
    """Raised when the intended active tab cannot be established exactly."""


def _runtime_id(value: Any, name: str) -> tuple[int, ...]:
    try:
        result = tuple(value)
    except Exception as exc:
        raise TerminalTabGuardError(f"{name} is unavailable") from exc
    if not result or any(type(item) is not int for item in result):
        raise TerminalTabGuardError(f"{name} must be a nonempty opaque integer sequence")
    return result


@dataclass(frozen=True)
class TerminalTabIdentity:
    """Operation-bound identity for one live Windows Terminal tab.

    Runtime IDs are deliberately labeled session-opaque. They are suitable for
    comparing a retained live UIA element, not for durable global identity.
    """

    window_hwnd: int
    window_pid: int
    window_process_start_time_ns: int
    tab_runtime_id: tuple[int, ...]
    term_control_runtime_id: tuple[int, ...]
    peer_birth_id: str
    runtime_id_scope: str = RUNTIME_ID_SCOPE

    def __post_init__(self) -> None:
        for value, name in (
            (self.window_hwnd, "window_hwnd"),
            (self.window_pid, "window_pid"),
            (self.window_process_start_time_ns, "window_process_start_time_ns"),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive exact integer")
        object.__setattr__(self, "tab_runtime_id", _runtime_id(self.tab_runtime_id, "tab_runtime_id"))
        object.__setattr__(
            self,
            "term_control_runtime_id",
            _runtime_id(self.term_control_runtime_id, "term_control_runtime_id"),
        )
        if type(self.peer_birth_id) is not str or _BIRTH_ID_RE.fullmatch(self.peer_birth_id) is None:
            raise ValueError("peer_birth_id must contain 1-128 safe ASCII identifier characters")
        if self.runtime_id_scope != RUNTIME_ID_SCOPE:
            raise ValueError("runtime_id_scope must declare the bounded UIA lifetime")


def _get_uia() -> tuple[Any, Any]:
    if __import__("os").name != "nt":
        raise TerminalTabGuardError("Windows UI Automation is required")
    try:
        import comtypes.client as cc  # type: ignore[import-untyped]

        module = cc.GetModule("UIAutomationCore.dll")
        uia = cc.CreateObject(
            "{FF48DBA4-60EF-4201-AA87-54103EEF594E}",
            interface=module.IUIAutomation,
        )
    except Exception as exc:
        raise TerminalTabGuardError("IUIAutomation initialization failed") from exc
    return uia, module


def _tab_items(uia: Any, root: Any) -> list[Any]:
    condition = uia.CreatePropertyCondition(_UIA_CONTROL_TYPE_PROPERTY_ID, _UIA_TAB_ITEM_CONTROL_TYPE_ID)
    found = root.FindAll(_UIA_TREE_SCOPE_SUBTREE, condition)
    return [found.GetElement(index) for index in range(found.Length)]


def _selected(item: Any) -> bool:
    try:
        return bool(item.GetCurrentPropertyValue(_UIA_SELECTION_ITEM_IS_SELECTED_PROPERTY_ID))
    except Exception as exc:
        raise TerminalTabGuardError("tab selected state is unavailable") from exc


def _active_term_control(uia: Any, module: Any, root: Any) -> Any:
    try:
        focused_element = uia.GetFocusedElement()
        walker = uia.ControlViewWalker
    except Exception as exc:
        raise TerminalTabGuardError("focused UIA element is unavailable") from exc

    def contains_focused(candidate: Any) -> bool:
        current = focused_element
        for _depth in range(64):
            try:
                if bool(uia.CompareElements(candidate, current)):
                    return True
                current = walker.GetParentElement(current)
            except Exception:
                return False
            if current is None:
                return False
        return False

    condition = uia.CreatePropertyCondition(_UIA_IS_TEXT_PATTERN_AVAILABLE_PROPERTY_ID, True)
    found = root.FindAll(_UIA_TREE_SCOPE_SUBTREE, condition)
    focused_candidates: list[Any] = []
    for index in range(found.Length):
        element = found.GetElement(index)
        try:
            element.GetCurrentPattern(_UIA_TEXT_PATTERN_ID).QueryInterface(
                module.IUIAutomationTextPattern
            )
        except Exception:
            continue
        if contains_focused(element):
            focused_candidates.append(element)
    if len(focused_candidates) == 1:
        return focused_candidates[0]
    raise TerminalTabGuardError("exactly one focused TermControl TextPattern is required")


def _root(uia: Any, identity: TerminalTabIdentity) -> Any:
    try:
        root = uia.ElementFromHandle(identity.window_hwnd)
        native_hwnd = int(root.GetCurrentPropertyValue(_UIA_NATIVE_WINDOW_HANDLE_PROPERTY_ID))
        process_id = int(root.GetCurrentPropertyValue(_UIA_PROCESS_ID_PROPERTY_ID))
    except Exception as exc:
        raise TerminalTabGuardError("Windows Terminal UIA root is unavailable") from exc
    if native_hwnd != identity.window_hwnd or process_id != identity.window_pid:
        raise TerminalTabGuardError("Windows Terminal UIA root identity changed")
    return root


class TerminalTabGuard:
    """Retains a live UIA tab element and checks active-tab identity."""

    __slots__ = ("identity", "_module", "_retained_tab", "_uia")

    def __init__(self, identity: TerminalTabIdentity, uia: Any, module: Any, retained_tab: Any) -> None:
        if type(identity) is not TerminalTabIdentity:
            raise TypeError("TerminalTabGuard requires an exact TerminalTabIdentity")
        self.identity = identity
        self._uia = uia
        self._module = module
        self._retained_tab = retained_tab

    def checkpoint(self, stage: str, *, select: bool, deadline: float) -> dict[str, Any]:
        if type(stage) is not str or not stage:
            raise ValueError("tab checkpoint stage is required")
        if time.monotonic() >= deadline:
            raise TimeoutError("active-tab checkpoint deadline expired")
        root = _root(self._uia, self.identity)
        items = _tab_items(self._uia, root)
        matches = [item for item in items if bool(self._uia.CompareElements(self._retained_tab, item))]
        if len(matches) != 1:
            raise TerminalTabGuardError("retained tab element is stale or ambiguous")
        current = matches[0]
        if _runtime_id(current.GetRuntimeId(), "tab RuntimeId") != self.identity.tab_runtime_id:
            raise TerminalTabGuardError("retained tab RuntimeId changed")
        if select and not _selected(current):
            try:
                pattern = current.GetCurrentPattern(_UIA_SELECTION_ITEM_PATTERN_ID).QueryInterface(
                    self._module.IUIAutomationSelectionItemPattern
                )
                pattern.Select()
            except Exception as exc:
                raise TerminalTabGuardError("SelectionItem.Select failed") from exc
            while time.monotonic() < deadline:
                root = _root(self._uia, self.identity)
                items = _tab_items(self._uia, root)
                selected_items = [item for item in items if _selected(item)]
                if len(selected_items) == 1 and bool(
                    self._uia.CompareElements(self._retained_tab, selected_items[0])
                ):
                    break
                time.sleep(0.01)
            else:
                raise TimeoutError("active-tab selection deadline expired")
        root = _root(self._uia, self.identity)
        items = _tab_items(self._uia, root)
        selected_items = [item for item in items if _selected(item)]
        if len(selected_items) != 1 or not bool(
            self._uia.CompareElements(self._retained_tab, selected_items[0])
        ):
            raise TerminalTabGuardError("intended tab is not the sole selected tab")
        term_control = _active_term_control(self._uia, self._module, root)
        term_runtime_id = _runtime_id(term_control.GetRuntimeId(), "TermControl RuntimeId")
        if term_runtime_id != self.identity.term_control_runtime_id:
            raise TerminalTabGuardError("active TermControl identity changed")
        return {
            "ok": True,
            "stage": stage,
            "retained_compare": True,
            "selected_count": 1,
            "selected": True,
            "tab_runtime_id": list(self.identity.tab_runtime_id),
            "term_control_runtime_id": list(term_runtime_id),
            "peer_birth_id": self.identity.peer_birth_id,
            "runtime_id_scope": RUNTIME_ID_SCOPE,
            "exclusive_routing_claimed": False,
        }


def capture_active_terminal_tab(target: Any, *, peer_birth_id: str) -> TerminalTabGuard:
    """Capture the currently selected tab as an operation-scoped guard.

    The caller enrolls the already-active tab. Title and tab index are never
    used as identity. The top-level target remains separately guarded by the
    guarded-submit TargetIdentity.
    """

    required = (
        "hwnd",
        "pid",
        "class_name",
        "process_start_time_ns",
    )
    if any(not hasattr(target, field) for field in required):
        raise TypeError("target must provide the guarded top-level identity")
    if target.class_name != "CASCADIA_HOSTING_WINDOW_CLASS":
        raise ValueError("active-tab guards require a Windows Terminal CASCADIA target")
    uia, module = _get_uia()
    provisional = TerminalTabIdentity(
        window_hwnd=int(target.hwnd),
        window_pid=int(target.pid),
        window_process_start_time_ns=int(target.process_start_time_ns),
        tab_runtime_id=(1,),
        term_control_runtime_id=(1,),
        peer_birth_id=peer_birth_id,
    )
    root = _root(uia, provisional)
    selected_items = [item for item in _tab_items(uia, root) if _selected(item)]
    if len(selected_items) != 1:
        raise TerminalTabGuardError("exactly one active Windows Terminal tab is required")
    selected_item = selected_items[0]
    term_control = _active_term_control(uia, module, root)
    identity = TerminalTabIdentity(
        window_hwnd=int(target.hwnd),
        window_pid=int(target.pid),
        window_process_start_time_ns=int(target.process_start_time_ns),
        tab_runtime_id=_runtime_id(selected_item.GetRuntimeId(), "tab RuntimeId"),
        term_control_runtime_id=_runtime_id(term_control.GetRuntimeId(), "TermControl RuntimeId"),
        peer_birth_id=peer_birth_id,
    )
    return TerminalTabGuard(identity, uia, module, selected_item)


__all__ = [
    "RUNTIME_ID_SCOPE",
    "TerminalTabGuard",
    "TerminalTabGuardError",
    "TerminalTabIdentity",
    "capture_active_terminal_tab",
]
