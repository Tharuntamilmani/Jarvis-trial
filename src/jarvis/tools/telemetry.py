"""Read-only local CPU/RAM/GPU/disk telemetry collector (Phase 7.3)."""

from __future__ import annotations

import asyncio
import ctypes
from dataclasses import asdict, dataclass
import shutil
import time
from typing import Any

from jarvis.tools.models import (
    ToolExecutionContext,
    ToolRiskLevel,
    ToolSpec,
)
from jarvis.tools.registry import ToolRegistry


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Point-in-time local system resource metrics."""

    cpu_percent: float
    ram_percent: float
    ram_used_mb: float
    ram_total_mb: float
    disk_percent: float
    gpu_percent: float | None = None
    sampled_at: float = 0.0


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("sullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class TelemetryCollector:
    """Bounded, read-only local hardware telemetry sampler."""

    def __init__(self, *, min_interval_seconds: float = 1.0) -> None:
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._last_snapshot: TelemetrySnapshot | None = None
        self._last_sampled_at = 0.0

    def sample(self, *, now: float | None = None) -> TelemetrySnapshot:
        current = time.monotonic() if now is None else now
        if (
            self._last_snapshot is not None
            and (current - self._last_sampled_at) < self._min_interval
        ):
            return self._last_snapshot

        cpu_pct = 0.0
        ram_pct = 0.0
        ram_used_mb = 0.0
        ram_total_mb = 0.0
        try:
            import psutil  # type: ignore[import-not-found]

            cpu_pct = float(psutil.cpu_percent(interval=None))
            vm = psutil.virtual_memory()
            ram_pct = float(vm.percent)
            ram_total_mb = float(vm.total) / (1024 * 1024)
            ram_used_mb = float(vm.used) / (1024 * 1024)
        except ImportError:
            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                ram_pct = float(stat.dwMemoryLoad)
                ram_total_mb = float(stat.ullTotalPhys) / (1024 * 1024)
                ram_used_mb = float(stat.ullTotalPhys - stat.ullAvailPhys) / (1024 * 1024)

        disk = shutil.disk_usage(".")
        disk_pct = (disk.used / disk.total * 100.0) if disk.total else 0.0

        snap = TelemetrySnapshot(
            cpu_percent=round(cpu_pct, 1),
            ram_percent=round(ram_pct, 1),
            ram_used_mb=round(ram_used_mb, 1),
            ram_total_mb=round(ram_total_mb, 1),
            disk_percent=round(disk_pct, 1),
            gpu_percent=None,
            sampled_at=current,
        )
        self._last_snapshot = snap
        self._last_sampled_at = current
        return snap


def register_telemetry_tools(
    registry: ToolRegistry, collector: TelemetryCollector | None = None
) -> None:
    """Register the read-only local ``sys.telemetry`` tool."""
    tel = collector or TelemetryCollector()

    async def _sample(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del args, ctx
        snap = await asyncio.to_thread(tel.sample)
        return asdict(snap)

    registry.register(
        ToolSpec(
            tool_id="sys.telemetry",
            input_schema={},
            output_schema={
                "cpu_percent": float,
                "ram_percent": float,
                "disk_percent": float,
            },
            capabilities=frozenset(),
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        _sample,
    )
