"""Privacy-bounded local Windows clipboard tools (Phase 7.3, zero PySide6)."""

from __future__ import annotations

import asyncio
import ctypes
from typing import Any, Protocol, runtime_checkable

from jarvis.tools.models import (
    ToolCapability,
    ToolExecutionContext,
    ToolExecutionError,
    ToolRiskLevel,
    ToolSpec,
)
from jarvis.tools.registry import ToolRegistry

_CF_UNICODETEXT = 13


@runtime_checkable
class ClipboardBackend(Protocol):
    """Pluggable clipboard backend."""

    def read_text(self) -> str: ...

    def write_text(self, text: str) -> None: ...

    def clear(self) -> None: ...


class WindowsClipboardBackend:
    """Win32 ctypes clipboard access without Qt dependencies."""

    def read_text(self) -> str:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(0):
            raise ToolExecutionError("Unable to open Windows clipboard")
        try:
            handle = user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def write_text(self, text: str) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        encoded = (text + "\x00").encode("utf-16-le")
        if not user32.OpenClipboard(0):
            raise ToolExecutionError("Unable to open Windows clipboard")
        try:
            user32.EmptyClipboard()
            hmem = kernel32.GlobalAlloc(0x0042, len(encoded))
            if not hmem:
                raise ToolExecutionError("Clipboard allocation failed")
            ptr = kernel32.GlobalLock(hmem)
            ctypes.memmove(ptr, encoded, len(encoded))
            kernel32.GlobalUnlock(hmem)
            user32.SetClipboardData(_CF_UNICODETEXT, hmem)
        finally:
            user32.CloseClipboard()

    def clear(self) -> None:
        user32 = ctypes.windll.user32
        if not user32.OpenClipboard(0):
            raise ToolExecutionError("Unable to open Windows clipboard")
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()


def register_clipboard_tools(
    registry: ToolRegistry,
    backend: ClipboardBackend | None = None,
    *,
    max_chars: int = 32_768,
) -> None:
    """Register clipboard read/write/clear tools with explicit approval."""
    clip = backend or WindowsClipboardBackend()

    async def _read(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del args, ctx
        text = await asyncio.to_thread(clip.read_text)
        return {"text": text[:max_chars], "truncated": len(text) > max_chars}

    async def _write(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        text = str(args["text"])
        if len(text) > max_chars:
            raise ToolExecutionError(
                f"Clipboard text exceeds max_chars ({len(text)} > {max_chars})"
            )
        await asyncio.to_thread(clip.write_text, text)
        return {"written": True, "length": len(text)}

    async def _clear(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del args, ctx
        await asyncio.to_thread(clip.clear)
        return {"cleared": True}

    registry.register(
        ToolSpec(
            tool_id="clipboard.read",
            input_schema={},
            output_schema={"text": str, "truncated": bool},
            capabilities=frozenset({ToolCapability.CLIPBOARD}),
            risk_level=ToolRiskLevel.STATE_CHANGING,
            requires_approval=True,
        ),
        _read,
    )
    registry.register(
        ToolSpec(
            tool_id="clipboard.write",
            input_schema={"text": str},
            output_schema={"written": bool, "length": int},
            capabilities=frozenset({ToolCapability.CLIPBOARD}),
            risk_level=ToolRiskLevel.STATE_CHANGING,
            requires_approval=True,
        ),
        _write,
    )
    registry.register(
        ToolSpec(
            tool_id="clipboard.clear",
            input_schema={},
            output_schema={"cleared": bool},
            capabilities=frozenset({ToolCapability.CLIPBOARD}),
            risk_level=ToolRiskLevel.DESTRUCTIVE,
            requires_approval=True,
        ),
        _clear,
    )
