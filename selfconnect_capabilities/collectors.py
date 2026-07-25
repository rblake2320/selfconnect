"""Bounded, read-only host collectors for Capability OS world state."""

from __future__ import annotations

import csv
import io
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass(frozen=True)
class CollectedObservation:
    key: str
    value: Any
    source: str
    ttl_seconds: float
    confidence: float = 1.0
    sensitive: bool = False


class HostCollectors:
    def __init__(
        self,
        *,
        mesh: str,
        window_reader: Callable[..., list[dict[str, Any]]],
        mesh_reader: Callable[[], dict[str, Any]],
        platform_reader: Callable[[], dict[str, Any]],
    ):
        self.mesh = mesh
        self.window_reader = window_reader
        self.mesh_reader = mesh_reader
        self.platform_reader = platform_reader

    def mesh_state(self) -> list[CollectedObservation]:
        roster = self.mesh_reader()
        agents = [
            {
                key: item.get(key)
                for key in (
                    "mesh", "role", "birth_id", "generation", "agent", "profile",
                    "status", "task", "transport", "model", "hwnd", "pid",
                    "exe_name", "class_name", "title",
                )
            }
            for item in roster.get("agents", [])[:200]
        ]
        return [CollectedObservation(
            f"mesh.{self.mesh}.roles",
            agents,
            "mesh-registry",
            15,
        )]

    def window_state(self) -> list[CollectedObservation]:
        windows = self.window_reader(query="", limit=200)
        bounded = [
            {
                key: item.get(key)
                for key in ("hwnd", "pid", "title", "exe_name", "class_name", "is_terminal", "visible")
            }
            for item in windows[:200]
        ]
        return [CollectedObservation(
            "windows.visible",
            bounded,
            "win32-enumwindows",
            5,
        )]

    def process_state(self) -> list[CollectedObservation]:
        rows = []
        for process in psutil.process_iter(("pid", "name", "status")):
            try:
                info = process.info
                rows.append({
                    "pid": int(info.get("pid", 0)),
                    "name": str(info.get("name", ""))[:260],
                    "status": str(info.get("status", "")),
                })
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            if len(rows) >= 1_000:
                break
        rows.sort(key=lambda item: (item["name"].casefold(), item["pid"]))
        return [CollectedObservation(
            "processes.inventory",
            rows,
            "psutil-process-iter",
            5,
        )]

    def service_state(self) -> list[CollectedObservation]:
        rows = []
        service_iter = getattr(psutil, "win_service_iter", None)
        if service_iter is None:
            return [CollectedObservation(
                "services.windows",
                [],
                "psutil-win-service-iter-unavailable",
                30,
                confidence=0.0,
            )]
        for service in service_iter():
            try:
                value = service.as_dict()
                rows.append({
                    "name": str(value.get("name", ""))[:260],
                    "display_name": str(value.get("display_name", ""))[:260],
                    "status": str(value.get("status", "")),
                    "start_type": str(value.get("start_type", "")),
                })
            except (psutil.AccessDenied, OSError):
                continue
            if len(rows) >= 1_000:
                break
        rows.sort(key=lambda item: item["name"].casefold())
        return [CollectedObservation(
            "services.windows",
            rows,
            "psutil-win-service-iter",
            15,
        )]

    def gpu_state(self) -> list[CollectedObservation]:
        command = [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
        try:
            output = subprocess.check_output(command, text=True, timeout=10)
            rows = []
            for row in csv.reader(io.StringIO(output)):
                if len(row) < 6:
                    continue
                rows.append({
                    "index": int(row[0].strip()),
                    "name": row[1].strip(),
                    "memory_total_mb": int(row[2].strip()),
                    "memory_used_mb": int(row[3].strip()),
                    "memory_free_mb": int(row[4].strip()),
                    "utilization_percent": int(row[5].strip()),
                })
            return [CollectedObservation("gpu.inventory", rows, "nvidia-smi", 3)]
        except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
            return [CollectedObservation(
                "gpu.inventory",
                {"available": False, "error": type(exc).__name__},
                "nvidia-smi-unavailable",
                30,
                confidence=0.0,
            )]

    def platform_state(self) -> list[CollectedObservation]:
        return [CollectedObservation(
            "platform.selfconnect.capabilities",
            self.platform_reader(),
            "selfconnect-doctor",
            300,
        )]

    def collect(self, scope: str) -> list[CollectedObservation]:
        methods = {
            "mesh": self.mesh_state,
            "windows": self.window_state,
            "processes": self.process_state,
            "services": self.service_state,
            "gpu": self.gpu_state,
            "platform": self.platform_state,
        }
        if scope == "all":
            observations = []
            for name in ("mesh", "windows", "processes", "services", "gpu", "platform"):
                observations.extend(methods[name]())
            return observations
        try:
            return methods[scope]()
        except KeyError as exc:
            raise ValueError(f"unknown collector scope: {scope}") from exc
