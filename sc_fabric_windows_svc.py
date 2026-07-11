"""Windows SCM (Service Control Manager) wrapper for SelfConnect Fabric V2.

Registers SelfConnectFabricV2 as a Windows service via pywin32.
Falls back gracefully when pywin32 is not installed.
"""

from __future__ import annotations

import sys
from typing import Any

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    _WIN32_SVC = True
except ImportError:
    _WIN32_SVC = False

SVC_NAME = "SelfConnectFabricV2"
SVC_DISPLAY = "SelfConnect Fabric V2 Service"
SVC_DESCRIPTION = (
    "Local Fabric V2 named-pipe IOCP host with restart-safe "
    "state persistence and agent watchdog."
)


if _WIN32_SVC:

    class SelfConnectFabricWinSvc(win32serviceutil.ServiceFramework):
        _svc_name_ = SVC_NAME
        _svc_display_name_ = SVC_DISPLAY
        _svc_description_ = SVC_DESCRIPTION

        def __init__(self, args: Any) -> None:
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._hWaitStop = win32event.CreateEvent(None, 0, 0, None)
            self._fabric: Any = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._hWaitStop)

        def SvcDoRun(self) -> None:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            import sc_fabric_service as svc_mod

            config = svc_mod.FabricServiceConfig()
            self._fabric = svc_mod.FabricService(config)
            try:
                self._fabric.start()
                win32event.WaitForSingleObject(self._hWaitStop, win32event.INFINITE)
            finally:
                if self._fabric is not None:
                    self._fabric.stop()
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )

else:

    class SelfConnectFabricWinSvc:  # type: ignore[no-redef]
        """Stub class when pywin32 is not available."""

        _svc_name_ = SVC_NAME
        _svc_display_name_ = SVC_DISPLAY
        _svc_description_ = SVC_DESCRIPTION


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return True if pywin32 is installed and we are on Windows."""
    return _WIN32_SVC and sys.platform == "win32"


def install_service() -> dict[str, Any]:
    """Install SelfConnectFabricV2 as an auto-start Windows service."""
    if not is_available():
        return {"ok": False, "error": "win32service not available"}
    try:
        win32serviceutil.InstallService(
            pythonClassString=f"{__name__}.SelfConnectFabricWinSvc",
            serviceName=SVC_NAME,
            displayName=SVC_DISPLAY,
            description=SVC_DESCRIPTION,
            startType=win32service.SERVICE_AUTO_START,
        )
        win32serviceutil.ChangeServiceConfig(
            pythonClassString=None,
            serviceName=SVC_NAME,
            serviceName2=SVC_NAME,
        )
        return {"ok": True, "action": "installed", "service_name": SVC_NAME}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def remove_service() -> dict[str, Any]:
    """Stop (if running) and remove the Windows service registration."""
    if not is_available():
        return {"ok": False, "error": "win32service not available"}
    try:
        win32serviceutil.StopService(SVC_NAME)
    except Exception:
        pass
    try:
        win32serviceutil.RemoveService(SVC_NAME)
        return {"ok": True, "action": "removed", "service_name": SVC_NAME}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def query_service() -> dict[str, Any]:
    """Return the current SCM state of the service."""
    if not is_available():
        return {"ok": False, "error": "win32service not available", "service_name": SVC_NAME}
    try:
        status = win32serviceutil.QueryServiceStatus(SVC_NAME)
        state_map = {
            win32service.SERVICE_STOPPED: "stopped",
            win32service.SERVICE_START_PENDING: "start_pending",
            win32service.SERVICE_STOP_PENDING: "stop_pending",
            win32service.SERVICE_RUNNING: "running",
            win32service.SERVICE_PAUSED: "paused",
        }
        state_code = status[1]
        return {
            "ok": True,
            "service_name": SVC_NAME,
            "display_name": SVC_DISPLAY,
            "state": state_map.get(state_code, f"unknown_{state_code}"),
            "state_code": state_code,
        }
    except Exception as exc:
        return {"ok": False, "service_name": SVC_NAME, "error": str(exc)}


def start_service() -> dict[str, Any]:
    """Send a start request to SCM for the service."""
    if not is_available():
        return {"ok": False, "error": "win32service not available"}
    try:
        win32serviceutil.StartService(SVC_NAME)
        return {"ok": True, "action": "start_requested", "service_name": SVC_NAME}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def stop_service() -> dict[str, Any]:
    """Send a stop request to SCM for the service."""
    if not is_available():
        return {"ok": False, "error": "win32service not available"}
    try:
        win32serviceutil.StopService(SVC_NAME)
        return {"ok": True, "action": "stop_requested", "service_name": SVC_NAME}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if _WIN32_SVC:
        win32serviceutil.HandleCommandLine(SelfConnectFabricWinSvc)
    else:
        print("win32service not available", file=sys.stderr)
        sys.exit(1)
