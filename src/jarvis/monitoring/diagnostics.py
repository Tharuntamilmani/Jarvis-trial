"""Privacy-safe unified diagnostics & release health check (Phase 8.7)."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import platform
import sys
from typing import TYPE_CHECKING, Any

from jarvis.security.audit import redact_audit_detail

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jarvis.core.engine import CoreEngine

_OPTIONAL_MODULES = {
    "cv2": "Install opencv-python for local camera capture",
    "insightface": "Install insightface for local face recognition",
    "mediapipe": "Install mediapipe for local gesture controls",
    "faster_whisper": "Install faster-whisper for local STT",
    "piper": "Install piper-tts for local TTS",
    "openwakeword": "Install openwakeword for offline wake-word detection",
    "psutil": "Install psutil for detailed CPU/RAM telemetry",
}


def check_optional_dependencies() -> dict[str, dict[str, Any]]:
    """Report optional dependency availability without eagerly importing them."""
    status: dict[str, dict[str, Any]] = {}
    for mod, hint in _OPTIONAL_MODULES.items():
        if mod in sys.modules:
            available = sys.modules[mod] is not None
        else:
            available = importlib.util.find_spec(mod) is not None
        status[mod] = {
            "available": available,
            "hint": "" if available else hint,
        }
    return status


class DiagnosticsService:
    """Collects and exports privacy-safe operational diagnostics across JARVIS."""

    def __init__(self, *, engine: CoreEngine | None = None) -> None:
        self._engine = engine

    async def collect(self) -> dict[str, Any]:
        """Aggregate sanitized diagnostics for all subsystems."""
        reg = self._engine.registry if self._engine is not None else None
        lifecycle = self._engine.lifecycle if self._engine is not None else None

        db = reg.try_get("database") if reg is not None else None
        db_ok = False
        if db is not None:
            try:
                db_ok = await db.health_check()
            except Exception:  # noqa: BLE001
                db_ok = False

        providers = (
            self._engine.provider_status_snapshot()
            if self._engine is not None
            else {}
        )
        ollama_status = providers.get("ollama")

        voice = reg.try_get("voice") if reg is not None else None
        vision = reg.try_get("vision") if reg is not None else None
        agents = reg.try_get("agents") if reg is not None else None
        scheduler = reg.try_get("scheduler") if reg is not None else None
        policy = reg.try_get("permissions") if reg is not None else None
        tools = reg.try_get("tools") if reg is not None else None

        raw_report: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "core_engine": {
                "safe_mode": getattr(self._engine, "safe_mode", False),
                "registered_services": sorted(reg.names) if reg is not None else [],
            },
            "postgresql": {
                "configured": db is not None,
                "healthy": db_ok,
            },
            "pgvector": {
                "vector_dim": (
                    self._engine.settings.embedding_dim
                    if self._engine is not None
                    else 0
                ),
            },
            "ollama": {
                "available": bool(ollama_status.available) if ollama_status else False,
                "model": ollama_status.model if ollama_status else "",
                "detail": ollama_status.detail if ollama_status else "not checked",
            },
            "voice": (
                voice.diagnostics_snapshot()
                if voice is not None and hasattr(voice, "diagnostics_snapshot")
                else {"initialized": voice is not None}
            ),
            "vision": (
                vision.diagnostics_snapshot()
                if vision is not None and hasattr(vision, "diagnostics_snapshot")
                else {"initialized": vision is not None}
            ),
            "agents": {
                "manager_initialized": agents is not None,
                "scheduler_initialized": scheduler is not None,
            },
            "security": {
                "kill_switch_active": (
                    lifecycle.kill_switch_active if lifecycle is not None else False
                ),
                "granted_capabilities": (
                    sorted(c.value for c in policy.granted_capabilities)
                    if policy is not None
                    else []
                ),
            },
            "tools": {
                "registered_count": (
                    len(tools.list_tools()) if tools is not None else 0
                ),
            },
            "packaging": {
                "version": "1.0.0",
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "optional_dependencies": check_optional_dependencies(),
        }
        return redact_audit_detail(raw_report)

    def export_report(self, report: dict[str, Any], target_path: Path) -> Path:
        """Write a redacted diagnostic JSON report to ``target_path``."""
        safe = redact_audit_detail(report)
        out = Path(target_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(safe, indent=2), encoding="utf-8")
        return out
