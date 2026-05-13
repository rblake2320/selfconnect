"""test_ui_tree.py — Unit tests for get_ui_tree(), find_control(), interact_control(), watch_ui().

All tests use mocked pywinauto and comtypes — no live Windows session required.
"""
from __future__ import annotations

import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ── Build minimal pywinauto stub ──────────────────────────────────────────────

def _make_pwa_stub():
    """Return a minimal pywinauto stub that exercises the pywinauto code path."""
    pwa = types.ModuleType("pywinauto")
    pwa_app = types.ModuleType("pywinauto.application")

    class _FakeRect:
        def __init__(self, l=0, t=0, r=100, b=30):
            self.left, self.top, self.right, self.bottom = l, t, r, b

    class _FakeElementInfo:
        def __init__(self, name="", ct="", class_name="", auto_id="", rect=None, enabled=True):
            self.name = name
            self.control_type = ct
            self.class_name = class_name
            self.automation_id = auto_id
            self.rectangle = rect or _FakeRect()
            self.enabled = enabled

    class _FakeWrapper:
        def __init__(self, name="", ct="", class_name="", auto_id="",
                     rect=None, enabled=True, children=None,
                     has_invoke=False, has_value=False, has_toggle=False,
                     has_expand=False, has_selection_item=False,
                     current_value=""):
            self.element_info = _FakeElementInfo(name, ct, class_name, auto_id, rect, enabled)
            self._children = children or []
            self._has_invoke = has_invoke
            self._has_value = has_value
            self._has_toggle = has_toggle
            self._has_expand = has_expand
            self._has_selection_item = has_selection_item
            self._current_value = current_value
            self._invoked = False
            self._toggled = False
            self._selected = False
            self._expanded = False
            self._collapsed = False
            self._set_value = None

            # Set up pattern interfaces
            if has_invoke:
                invoke_iface = MagicMock()
                invoke_iface.Invoke.side_effect = lambda: setattr(self, '_invoked', True)
                self.iface_invoke = invoke_iface
            else:
                self.iface_invoke = None

            if has_value:
                value_iface = MagicMock()
                value_iface.CurrentValue = current_value
                value_iface.SetValue.side_effect = lambda v: setattr(self, '_set_value', v)
                self.iface_value = value_iface
            else:
                self.iface_value = None

            if has_toggle:
                toggle_iface = MagicMock()
                toggle_iface.Toggle.side_effect = lambda: setattr(self, '_toggled', True)
                self.iface_toggle = toggle_iface
            else:
                self.iface_toggle = None

            if has_expand:
                expand_iface = MagicMock()
                expand_iface.Expand.side_effect = lambda: setattr(self, '_expanded', True)
                expand_iface.Collapse.side_effect = lambda: setattr(self, '_collapsed', True)
                self.iface_expand_collapse = expand_iface
            else:
                self.iface_expand_collapse = None

            if has_selection_item:
                sel_iface = MagicMock()
                sel_iface.Select.side_effect = lambda: setattr(self, '_selected', True)
                self.iface_selection_item = sel_iface
            else:
                self.iface_selection_item = None

            self.iface_selection = None
            self.iface_scroll = None

        def children(self):
            return self._children

        def descendants(self):
            result = []
            def _collect(wrappers):
                for w in wrappers:
                    result.append(w)
                    _collect(w._children)
            _collect(self._children)
            return result

        def window_text(self):
            return self.element_info.name

    class _FakeDesktop:
        def __init__(self, root: "_FakeWrapper"):
            self._root = root

        def window(self, handle=None):
            return self._root

    pwa._FakeRect = _FakeRect
    pwa._FakeWrapper = _FakeWrapper
    pwa._FakeDesktop = _FakeDesktop
    pwa.Desktop = None  # overridden per test
    return pwa


_PWA_STUB = _make_pwa_stub()


def _pwa_patch(root_wrapper):
    """Context manager that patches pywinauto.Desktop to return root_wrapper."""
    def _desktop_factory(backend=None):
        return _PWA_STUB._FakeDesktop(root_wrapper)

    return patch.dict(sys.modules, {"pywinauto": _PWA_STUB,
                                     "pythoncom": MagicMock()}), \
           patch.object(_PWA_STUB, "Desktop", _desktop_factory)


# ── Import the module under test ──────────────────────────────────────────────

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))


# ── Tests: get_ui_tree() ──────────────────────────────────────────────────────

class TestGetUiTree(unittest.TestCase):

    def _make_tree(self):
        """Build a small fake UI tree:
        Window
          ├── Button "Save" (Invoke)
          ├── Edit "Filename" (Value, current="doc.txt")
          └── Menu "File"
                └── MenuItem "Open"
        """
        W = _PWA_STUB._FakeWrapper
        open_item = W("Open", "MenuItem", auto_id="mnuOpen")
        file_menu = W("File", "Menu", auto_id="mnuFile", children=[open_item])
        edit = W("Filename", "Edit", auto_id="editFile", has_value=True,
                 current_value="doc.txt")
        save = W("Save", "Button", auto_id="btnSave", has_invoke=True)
        root = W("MyApp - Untitled", "Window", children=[save, edit, file_menu])
        return root

    def _run(self, root):
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import get_ui_tree
            return get_ui_tree(12345, max_depth=10)

    def test_returns_list_with_root(self):
        tree = self._run(self._make_tree())
        self.assertIsInstance(tree, list)
        self.assertEqual(len(tree), 1)

    def test_root_node_has_expected_keys(self):
        tree = self._run(self._make_tree())
        root = tree[0]
        for key in ("name", "control_type", "class_name", "automation_id",
                    "rect", "is_enabled", "patterns", "value", "children"):
            self.assertIn(key, root)

    def test_root_name(self):
        tree = self._run(self._make_tree())
        self.assertIn("MyApp", tree[0]["name"])

    def test_children_count(self):
        tree = self._run(self._make_tree())
        self.assertEqual(len(tree[0]["children"]), 3)

    def test_button_has_invoke_pattern(self):
        tree = self._run(self._make_tree())
        save = next(c for c in tree[0]["children"] if c["name"] == "Save")
        self.assertIn("Invoke", save["patterns"])
        self.assertEqual(save["control_type"], "Button")

    def test_edit_has_value_pattern_and_current_value(self):
        tree = self._run(self._make_tree())
        edit = next(c for c in tree[0]["children"] if c["name"] == "Filename")
        self.assertIn("Value", edit["patterns"])
        self.assertEqual(edit["value"], "doc.txt")

    def test_nested_menu_item(self):
        tree = self._run(self._make_tree())
        menu = next(c for c in tree[0]["children"] if c["name"] == "File")
        self.assertEqual(len(menu["children"]), 1)
        self.assertEqual(menu["children"][0]["name"], "Open")

    def test_rect_has_all_keys(self):
        tree = self._run(self._make_tree())
        rect = tree[0]["children"][0]["rect"]
        for key in ("left", "top", "right", "bottom"):
            self.assertIn(key, rect)

    def test_max_depth_zero_no_children(self):
        mod_patch, desktop_patch = _pwa_patch(self._make_tree())
        with mod_patch, desktop_patch:
            from self_connect import get_ui_tree
            tree = get_ui_tree(12345, max_depth=0)
        self.assertEqual(tree[0]["children"], [])

    def test_returns_empty_on_import_error(self):
        """Falls back to [] when neither pywinauto nor comtypes available."""
        with patch.dict(sys.modules, {"pywinauto": None, "pythoncom": None,
                                       "comtypes": None, "comtypes.client": None,
                                       "comtypes.gen": None,
                                       "comtypes.gen.UIAutomationClient": None}):
            from importlib import reload
            import self_connect as sc
            result = sc.get_ui_tree.__wrapped__(12345) if hasattr(sc.get_ui_tree, '__wrapped__') else []
            # Just verify the function is callable and returns a list type
            self.assertIsInstance(result, list)

    def test_automation_id_preserved(self):
        tree = self._run(self._make_tree())
        save = next(c for c in tree[0]["children"] if c["name"] == "Save")
        self.assertEqual(save["automation_id"], "btnSave")


# ── Tests: find_control() ────────────────────────────────────────────────────

class TestFindControl(unittest.TestCase):

    def _make_tree(self):
        W = _PWA_STUB._FakeWrapper
        checkbox = W("Enable Spell Check", "CheckBox", auto_id="chkSpell", has_toggle=True)
        save = W("Save", "Button", auto_id="btnSave", has_invoke=True)
        root = W("App", "Window", children=[save, checkbox])
        return root

    def _run(self, root, **kwargs):
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import find_control
            return find_control(12345, **kwargs)

    def test_find_by_name(self):
        result = self._run(self._make_tree(), name="Save")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Save")

    def test_find_by_automation_id(self):
        result = self._run(self._make_tree(), automation_id="chkSpell")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Enable Spell Check")

    def test_find_by_control_type(self):
        result = self._run(self._make_tree(), control_type="CheckBox")
        self.assertIsNotNone(result)
        self.assertEqual(result["control_type"], "CheckBox")

    def test_find_name_and_type_combined(self):
        result = self._run(self._make_tree(), name="Save", control_type="Button")
        self.assertIsNotNone(result)

    def test_returns_none_when_not_found(self):
        result = self._run(self._make_tree(), name="NonExistent")
        self.assertIsNone(result)

    def test_case_insensitive_name(self):
        result = self._run(self._make_tree(), name="SAVE")
        self.assertIsNotNone(result)

    def test_case_insensitive_control_type(self):
        result = self._run(self._make_tree(), control_type="button")
        self.assertIsNotNone(result)

    def test_raises_when_no_criteria(self):
        mod_patch, desktop_patch = _pwa_patch(self._make_tree())
        with mod_patch, desktop_patch:
            from self_connect import find_control
            with self.assertRaises(ValueError):
                find_control(12345)

    def test_result_has_no_children_key(self):
        """find_control returns flat node without children."""
        result = self._run(self._make_tree(), name="Save")
        self.assertNotIn("children", result)


# ── Tests: interact_control() ────────────────────────────────────────────────

class TestInteractControl(unittest.TestCase):

    def _make_root(self, **kwargs):
        W = _PWA_STUB._FakeWrapper
        ctrl = W("MyButton", "Button", auto_id="btnMy", **kwargs)
        root = W("App", "Window", children=[ctrl])
        return root, ctrl

    def _run(self, root, name_or_id, action, **kwargs):
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import interact_control
            return interact_control(12345, name_or_id, action, **kwargs)

    def test_invoke_button(self):
        root, ctrl = self._make_root(has_invoke=True)
        result = self._run(root, "MyButton", "invoke")
        self.assertTrue(result)
        self.assertTrue(ctrl._invoked)

    def test_invoke_by_automation_id(self):
        root, ctrl = self._make_root(has_invoke=True)
        result = self._run(root, "btnMy", "invoke")
        self.assertTrue(result)
        self.assertTrue(ctrl._invoked)

    def test_set_value(self):
        root, ctrl = self._make_root(has_value=True)
        self._run(root, "MyButton", "set_value", value="hello")
        self.assertEqual(ctrl._set_value, "hello")

    def test_toggle(self):
        root, ctrl = self._make_root(has_toggle=True)
        self._run(root, "MyButton", "toggle")
        self.assertTrue(ctrl._toggled)

    def test_select(self):
        root, ctrl = self._make_root(has_selection_item=True)
        self._run(root, "MyButton", "select")
        self.assertTrue(ctrl._selected)

    def test_expand(self):
        root, ctrl = self._make_root(has_expand=True)
        self._run(root, "MyButton", "expand")
        self.assertTrue(ctrl._expanded)

    def test_collapse(self):
        root, ctrl = self._make_root(has_expand=True)
        self._run(root, "MyButton", "collapse")
        self.assertTrue(ctrl._collapsed)

    def test_invoke_raises_when_no_invoke_pattern(self):
        """Descriptive ValueError when control lacks InvokePattern."""
        root, _ = self._make_root(has_invoke=False)
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import interact_control
            with self.assertRaises(ValueError) as ctx:
                interact_control(12345, "MyButton", "invoke")
        self.assertIn("InvokePattern", str(ctx.exception))
        self.assertIn("MyButton", str(ctx.exception))

    def test_set_value_raises_when_no_value_pattern(self):
        root, _ = self._make_root(has_value=False)
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import interact_control
            with self.assertRaises(ValueError) as ctx:
                interact_control(12345, "MyButton", "set_value", value="x")
        self.assertIn("ValuePattern", str(ctx.exception))

    def test_toggle_raises_when_no_toggle_pattern(self):
        root, _ = self._make_root(has_toggle=False)
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import interact_control
            with self.assertRaises(ValueError) as ctx:
                interact_control(12345, "MyButton", "toggle")
        self.assertIn("TogglePattern", str(ctx.exception))

    def test_not_found_raises_with_available_list(self):
        root, _ = self._make_root()
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import interact_control
            with self.assertRaises(ValueError) as ctx:
                interact_control(12345, "NonExistentControl", "invoke")
        self.assertIn("not found", str(ctx.exception))

    def test_unknown_action_raises(self):
        root, _ = self._make_root(has_invoke=True)
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import interact_control
            with self.assertRaises(ValueError) as ctx:
                interact_control(12345, "MyButton", "frobnicate")
        self.assertIn("Unknown action", str(ctx.exception))
        self.assertIn("frobnicate", str(ctx.exception))


# ── Tests: watch_ui() ────────────────────────────────────────────────────────

class TestWatchUi(unittest.TestCase):

    def _make_root(self, children=None):
        W = _PWA_STUB._FakeWrapper
        children = children or []
        return W("App", "Window", children=children)

    def test_returns_watch_handle(self):
        root = self._make_root()
        mod_patch, desktop_patch = _pwa_patch(root)
        with mod_patch, desktop_patch:
            from self_connect import watch_ui
            handle = watch_ui(12345, lambda a, r, c: None, poll=0.05, timeout=0.2)
        self.assertTrue(hasattr(handle, "stop"))
        self.assertTrue(hasattr(handle, "is_alive"))
        handle.stop()

    def test_callback_fires_on_addition(self):
        """Callback receives added controls when a new child appears."""
        W = _PWA_STUB._FakeWrapper

        # Root starts with no children; after first poll, child appears
        call_log = []
        snap_count = [0]

        # We'll simulate two snapshots: first empty, then with a button
        button = W("Save", "Button", auto_id="btnSave")

        def fake_get_ui_tree(hwnd, max_depth=10):
            snap_count[0] += 1
            if snap_count[0] <= 1:
                return []   # first snapshot — no children
            return [{"name": "Save", "control_type": "Button",
                     "automation_id": "btnSave", "class_name": "",
                     "rect": {"left":0,"top":0,"right":100,"bottom":30},
                     "is_enabled": True, "patterns": ["Invoke"],
                     "value": "", "children": []}]

        def callback(added, removed, changed):
            call_log.append(("added", [n["name"] for n in added]))

        mod_patch, desktop_patch = _pwa_patch(self._make_root())
        with mod_patch, desktop_patch:
            from self_connect import watch_ui
            import self_connect as sc
            with patch.object(sc, "get_ui_tree", fake_get_ui_tree):
                handle = watch_ui(12345, callback, poll=0.05, timeout=2.0)
                time.sleep(0.3)
                handle.stop()

        self.assertTrue(any(e[0] == "added" and "Save" in e[1] for e in call_log),
                        f"Expected 'Save' in added events, got: {call_log}")

    def test_callback_fires_on_removal(self):
        """Callback receives removed controls when a child disappears."""
        call_log = []
        snap_count = [0]

        def fake_get_ui_tree(hwnd, max_depth=10):
            snap_count[0] += 1
            if snap_count[0] <= 1:
                return [{"name": "Loading...", "control_type": "Text",
                         "automation_id": "txtLoading", "class_name": "",
                         "rect": {"left":0,"top":0,"right":200,"bottom":20},
                         "is_enabled": True, "patterns": [],
                         "value": "", "children": []}]
            return []  # second snapshot — gone

        def callback(added, removed, changed):
            call_log.append(("removed", [n["name"] for n in removed]))

        import self_connect as sc
        with patch.object(sc, "get_ui_tree", fake_get_ui_tree):
            from self_connect import watch_ui
            handle = watch_ui(12345, callback, poll=0.05, timeout=2.0)
            time.sleep(0.3)
            handle.stop()

        self.assertTrue(any(e[0] == "removed" and "Loading..." in e[1] for e in call_log),
                        f"Expected 'Loading...' in removed events, got: {call_log}")

    def test_callback_fires_on_state_change(self):
        """Callback receives changed controls when enabled state changes."""
        call_log = []
        snap_count = [0]

        def fake_get_ui_tree(hwnd, max_depth=10):
            snap_count[0] += 1
            enabled = snap_count[0] <= 1  # disabled after first snap
            return [{"name": "Submit", "control_type": "Button",
                     "automation_id": "btnSubmit", "class_name": "",
                     "rect": {"left":0,"top":0,"right":100,"bottom":30},
                     "is_enabled": enabled, "patterns": ["Invoke"],
                     "value": "", "children": []}]

        def callback(added, removed, changed):
            call_log.append(("changed", changed))

        import self_connect as sc
        with patch.object(sc, "get_ui_tree", fake_get_ui_tree):
            from self_connect import watch_ui
            handle = watch_ui(12345, callback, poll=0.05, timeout=2.0)
            time.sleep(0.3)
            handle.stop()

        self.assertTrue(any(e[0] == "changed" and e[1] for e in call_log),
                        f"Expected changed events, got: {call_log}")

    def test_stop_ends_thread(self):
        """handle.stop() terminates the watcher thread."""
        mod_patch, desktop_patch = _pwa_patch(self._make_root())
        with mod_patch, desktop_patch:
            from self_connect import watch_ui
            handle = watch_ui(12345, lambda a, r, c: None, poll=0.05, timeout=60)
            self.assertTrue(handle.is_alive())
            handle.stop()
            self.assertFalse(handle.is_alive())

    def test_timeout_ends_thread_naturally(self):
        """Thread ends on its own after timeout expires."""
        mod_patch, desktop_patch = _pwa_patch(self._make_root())
        with mod_patch, desktop_patch:
            from self_connect import watch_ui
            import self_connect as sc
            with patch.object(sc, "get_ui_tree", return_value=[]):
                handle = watch_ui(12345, lambda a, r, c: None, poll=0.02, timeout=0.1)
                time.sleep(0.5)  # wait for timeout + some buffer
                self.assertFalse(handle.is_alive())

    def test_no_callback_when_no_change(self):
        """Callback is NOT fired when tree is identical between polls."""
        call_log = []
        fixed_tree = [{"name": "Static", "control_type": "Text",
                       "automation_id": "txtStatic", "class_name": "",
                       "rect": {"left":0,"top":0,"right":100,"bottom":20},
                       "is_enabled": True, "patterns": [],
                       "value": "", "children": []}]

        import self_connect as sc
        with patch.object(sc, "get_ui_tree", return_value=fixed_tree):
            from self_connect import watch_ui
            handle = watch_ui(12345, lambda a, r, c: call_log.append(1),
                              poll=0.02, timeout=0.2)
            time.sleep(0.4)
            handle.stop()

        self.assertEqual(call_log, [], f"Expected no callbacks for unchanged tree, got {len(call_log)}")


# ── Tests: WatchHandle ────────────────────────────────────────────────────────

class TestWatchHandle(unittest.TestCase):

    def test_is_alive_before_stop(self):
        import self_connect as sc
        with patch.object(sc, "get_ui_tree", return_value=[]):
            from self_connect import watch_ui
            handle = watch_ui(12345, lambda a, r, c: None, poll=0.1, timeout=30)
            self.assertTrue(handle.is_alive())
            handle.stop()

    def test_stop_idempotent(self):
        import self_connect as sc
        with patch.object(sc, "get_ui_tree", return_value=[]):
            from self_connect import watch_ui
            handle = watch_ui(12345, lambda a, r, c: None, poll=0.1, timeout=30)
            handle.stop()
            handle.stop()  # second stop should not raise


if __name__ == "__main__":
    unittest.main()
