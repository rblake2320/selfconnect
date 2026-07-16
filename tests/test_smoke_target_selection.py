from types import SimpleNamespace

from test_self_connect import _select_unique_external_window


def _window(hwnd: int, pid: int, title: str):
    return SimpleNamespace(hwnd=hwnd, pid=pid, title=title)


def test_select_unique_external_window_excludes_own_pid():
    own = _window(100, 10, "GitHub Actions Runner")
    external = _window(200, 20, "Windows PowerShell")

    candidate, query = _select_unique_external_window([own, external], own_pid=10)

    assert candidate is external
    assert query == "Windows PowerShell"


def test_select_unique_external_window_skips_duplicate_titles():
    duplicate_a = _window(100, 20, "Administrator: PowerShell")
    duplicate_b = _window(200, 30, "Administrator: PowerShell")
    unique = _window(300, 40, "Server Manager")

    candidate, query = _select_unique_external_window(
        [duplicate_a, duplicate_b, unique],
        own_pid=10,
    )

    assert candidate is unique
    assert query == "Server Manager"


def test_select_unique_external_window_returns_none_without_safe_probe():
    own = _window(100, 10, "Runner")
    duplicate_a = _window(200, 20, "PowerShell")
    duplicate_b = _window(300, 30, "PowerShell")
    untitled = _window(400, 40, "   ")

    candidate, query = _select_unique_external_window(
        [own, duplicate_a, duplicate_b, untitled],
        own_pid=10,
    )

    assert candidate is None
    assert query == ""
