"""Tests for sc_fabric_windows_svc — Windows SCM wrapper.

These tests do NOT require admin privileges or an actual service install.
They cover the public API surface and degrade gracefully when pywin32 is absent.
"""

from __future__ import annotations

from unittest import mock

# ---------------------------------------------------------------------------
# 1. query_service returns a useful dict regardless of install state
# ---------------------------------------------------------------------------


def test_query_service_returns_dict_when_not_installed() -> None:
    import sc_fabric_windows_svc as winsvc

    result = winsvc.query_service()
    assert isinstance(result, dict)
    assert "service_name" in result
    # Either a successful query or an error dict — both are acceptable.
    assert result["service_name"] == winsvc.SVC_NAME


# ---------------------------------------------------------------------------
# 2. is_available() always returns a bool
# ---------------------------------------------------------------------------


def test_is_available_returns_bool() -> None:
    import sc_fabric_windows_svc as winsvc

    result = winsvc.is_available()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 3. install / remove return ok=False when _WIN32_SVC is patched False
# ---------------------------------------------------------------------------


def test_install_remove_noop_when_not_available() -> None:
    import sc_fabric_windows_svc as winsvc

    with mock.patch.object(winsvc, "_WIN32_SVC", False):
        install_result = winsvc.install_service()
        remove_result = winsvc.remove_service()

    assert install_result == {"ok": False, "error": "win32service not available"}
    assert remove_result == {"ok": False, "error": "win32service not available"}


def test_install_uses_supported_pywin32_arguments() -> None:
    import sc_fabric_windows_svc as winsvc

    with (
        mock.patch.object(winsvc, "_WIN32_SVC", True),
        mock.patch.object(winsvc.sys, "platform", "win32"),
        mock.patch.object(winsvc.win32serviceutil, "InstallService") as install,
    ):
        result = winsvc.install_service()

    assert result == {
        "ok": True,
        "action": "installed",
        "service_name": winsvc.SVC_NAME,
    }
    install.assert_called_once_with(
        pythonClassString="sc_fabric_windows_svc.SelfConnectFabricWinSvc",
        serviceName=winsvc.SVC_NAME,
        displayName=winsvc.SVC_DISPLAY,
        description=winsvc.SVC_DESCRIPTION,
        startType=winsvc.win32service.SERVICE_AUTO_START,
    )


def test_service_stop_wait_is_bounded() -> None:
    import sc_fabric_windows_svc as winsvc

    with mock.patch.object(
        winsvc.win32event,
        "WaitForSingleObject",
        side_effect=[winsvc.win32event.WAIT_TIMEOUT, winsvc.win32event.WAIT_OBJECT_0],
    ) as wait:
        winsvc._wait_for_stop(object(), poll_ms=125)

    assert wait.call_count == 2
    assert all(call.args[1] == 125 for call in wait.call_args_list)


# ---------------------------------------------------------------------------
# 4. Module-level constants are present and correct
# ---------------------------------------------------------------------------


def test_service_module_imports_clean() -> None:
    import sc_fabric_windows_svc

    assert hasattr(sc_fabric_windows_svc, "SVC_NAME")
    assert sc_fabric_windows_svc.SVC_NAME == "SelfConnectFabricV2"
    assert hasattr(sc_fabric_windows_svc, "SVC_DISPLAY")
    assert hasattr(sc_fabric_windows_svc, "SVC_DESCRIPTION")
    assert hasattr(sc_fabric_windows_svc, "SelfConnectFabricWinSvc")


# ---------------------------------------------------------------------------
# 5. CLI parser registers the five new service subcommands
# ---------------------------------------------------------------------------


def test_service_cli_subcommands_registered() -> None:
    import sc_fabric_service

    parser = sc_fabric_service._build_parser()
    # The last _SubParsersAction holds all registered subcommand names.
    choices = parser._subparsers._actions[-1].choices  # type: ignore[attr-defined]
    assert "install-service" in choices
    assert "remove-service" in choices
    assert "query-service" in choices
    assert "start-service" in choices
    assert "stop-service" in choices
