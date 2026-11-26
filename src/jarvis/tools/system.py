"""Allowlisted Windows app launcher & in-memory screenshot/OCR (Phase 7.3)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.tools.models import (
    ToolCapability,
    ToolExecutionContext,
    ToolExecutionError,
    ToolPermissionDeniedError,
    ToolRiskLevel,
    ToolSpec,
)
from jarvis.tools.registry import ToolRegistry

AppLauncher = Callable[[Path, list[str]], Awaitable[int]]
ScreenshotProvider = Callable[[], bytes]
LocalOCRProvider = Callable[[bytes], str]


@dataclass(frozen=True)
class AllowedApp:
    """Explicitly allowlisted Windows executable & permitted flags."""

    app_id: str
    executable: Path
    allowed_args: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SystemToolsConfig:
    """Configuration for system app launching and in-memory screenshot OCR."""

    allowed_apps: dict[str, AllowedApp] = field(default_factory=dict)
    max_screenshot_bytes: int = 4_194_304


async def _default_launcher(executable: Path, args: list[str]) -> int:
    # Never uses shell=True or raw shell command strings.
    proc = await asyncio.create_subprocess_exec(
        str(executable),
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return int(proc.pid)


def _default_screenshot() -> bytes:
    raise ToolExecutionError("No local screenshot provider configured")


def _default_ocr(image_bytes: bytes) -> str:
    del image_bytes
    raise ToolExecutionError("No local OCR provider configured")


def register_system_tools(
    registry: ToolRegistry,
    config: SystemToolsConfig,
    *,
    launcher: AppLauncher = _default_launcher,
    screenshot_provider: ScreenshotProvider = _default_screenshot,
    ocr_provider: LocalOCRProvider = _default_ocr,
) -> None:
    """Register allowlisted app launcher and local-only screenshot OCR tools."""

    async def _list_apps(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del args, ctx
        return {"apps": sorted(config.allowed_apps.keys())}

    async def _launch_app(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        app_id = str(args["app_id"]).strip()
        app = config.allowed_apps.get(app_id)
        if app is None:
            raise ToolPermissionDeniedError(
                f"Application {app_id!r} is not registered in allowed_apps"
            )
        raw_args = args.get("args", [])
        validated_args: list[str] = []
        for item in raw_args:
            flag = str(item)
            if flag not in app.allowed_args:
                raise ToolPermissionDeniedError(
                    f"Argument {flag!r} is not allowlisted for {app_id!r}"
                )
            validated_args.append(flag)

        pid = await launcher(app.executable, validated_args)
        return {"app_id": app_id, "pid": pid}

    async def _screenshot_ocr(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del args, ctx
        buf = bytearray(await asyncio.to_thread(screenshot_provider))
        try:
            if len(buf) > config.max_screenshot_bytes:
                raise ToolExecutionError("Screenshot exceeds memory bound")
            text = await asyncio.to_thread(ocr_provider, bytes(buf))
            return {"text": text, "bytes_processed": len(buf)}
        finally:
            for idx in range(len(buf)):
                buf[idx] = 0

    registry.register(
        ToolSpec(
            tool_id="sys.list_apps",
            input_schema={},
            output_schema={"apps": list},
            capabilities=frozenset({ToolCapability.PROCESS}),
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        _list_apps,
    )
    registry.register(
        ToolSpec(
            tool_id="sys.launch_app",
            input_schema={"app_id": str, "args": list},
            output_schema={"app_id": str, "pid": int},
            optional_params=frozenset({"args"}),
            capabilities=frozenset({ToolCapability.PROCESS}),
            risk_level=ToolRiskLevel.STATE_CHANGING,
            command_param="app_id",
        ),
        _launch_app,
    )
    registry.register(
        ToolSpec(
            tool_id="sys.screenshot_ocr",
            input_schema={},
            output_schema={"text": str, "bytes_processed": int},
            capabilities=frozenset({ToolCapability.SYSTEM_SETTINGS}),
            risk_level=ToolRiskLevel.STATE_CHANGING,
        ),
        _screenshot_ocr,
    )
