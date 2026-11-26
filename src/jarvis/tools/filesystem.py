"""Safe workspace-scoped filesystem tools (Phase 7.3, zero PySide6)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
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


@dataclass(frozen=True)
class FilesystemConfig:
    """Strict root and size bounds for filesystem tools."""

    allowed_roots: tuple[Path, ...]
    max_read_bytes: int = 262_144
    max_search_results: int = 100
    max_depth: int = 8


def _has_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if callable(is_junction) and is_junction(path):
        return True
    return False


def resolve_safe_path(
    raw_path: str, allowed_roots: tuple[Path, ...]
) -> Path:
    """Validate and resolve ``raw_path`` strictly inside ``allowed_roots``."""
    if not allowed_roots:
        raise ToolPermissionDeniedError("No allowed filesystem roots configured")
    clean = raw_path.strip()
    if not clean or "\x00" in clean:
        raise ToolPermissionDeniedError("Invalid empty or null-byte path")
    if clean.startswith("\\\\") or clean.startswith("//"):
        raise ToolPermissionDeniedError("UNC network paths are prohibited")

    candidate = Path(clean)
    if ".." in candidate.parts:
        raise ToolPermissionDeniedError("Parent-directory traversal ('..') is prohibited")

    if not candidate.is_absolute():
        candidate = allowed_roots[0] / candidate

    # Walk existing segments (including dangling symlinks) to reject symlinks/junctions.
    for parent in (candidate, *candidate.parents):
        if os.path.lexists(parent) and _has_reparse_or_symlink(parent):
            raise ToolPermissionDeniedError(
                f"Symbolic links or junctions are prohibited: {parent}"
            )

    resolved_roots = tuple(r.resolve(strict=False) for r in allowed_roots)
    resolved = candidate.resolve(strict=False)
    if not any(resolved.is_relative_to(root) for root in resolved_roots):
        raise ToolPermissionDeniedError(
            f"Path escapes allowed filesystem roots: {resolved}"
        )
    return resolved


def register_filesystem_tools(
    registry: ToolRegistry, config: FilesystemConfig
) -> None:
    """Register typed read/list/search/mkdir/move/delete tools."""
    roots = tuple(config.allowed_roots)

    async def _fs_read(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        target = resolve_safe_path(args["path"], roots)
        if not target.is_file():
            raise ToolExecutionError(f"File does not exist: {target.name}")
        size = target.stat().st_size
        if size > config.max_read_bytes:
            raise ToolExecutionError(
                f"File exceeds max_read_bytes ({size} > {config.max_read_bytes})"
            )
        content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        return {"path": str(target), "content": content}

    async def _fs_list(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        target = resolve_safe_path(args["path"], roots)
        if not target.is_dir():
            raise ToolExecutionError(f"Directory does not exist: {target.name}")
        entries = sorted(
            p.name
            for p in target.iterdir()
            if not _has_reparse_or_symlink(p)
        )
        return {"path": str(target), "entries": entries}

    async def _fs_search(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        root = resolve_safe_path(args["path"], roots)
        pattern = str(args["pattern"]).lower()
        if not root.is_dir():
            raise ToolExecutionError(f"Directory does not exist: {root.name}")
        matches: list[str] = []

        def _walk() -> None:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                current = Path(dirpath)
                rel_depth = len(current.relative_to(root).parts)
                if rel_depth >= config.max_depth:
                    dirnames[:] = []
                dirnames[:] = [
                    d for d in dirnames if not _has_reparse_or_symlink(current / d)
                ]
                for fname in filenames:
                    if pattern in fname.lower():
                        matches.append(str(current / fname))
                        if len(matches) >= config.max_search_results:
                            return

        await asyncio.to_thread(_walk)
        return {"matches": matches}

    async def _fs_mkdir(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        target = resolve_safe_path(args["path"], roots)
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        return {"path": str(target), "created": True}

    async def _fs_move(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        src = resolve_safe_path(args["src"], roots)
        dst = resolve_safe_path(args["dst"], roots)
        if not src.exists():
            raise ToolExecutionError(f"Source does not exist: {src.name}")
        if dst.exists():
            raise ToolExecutionError(f"Destination already exists: {dst.name}")
        await asyncio.to_thread(src.replace, dst)
        return {"src": str(src), "dst": str(dst), "moved": True}

    async def _fs_delete(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> dict[str, Any]:
        del ctx
        target = resolve_safe_path(args["path"], roots)
        if not target.is_file():
            raise ToolExecutionError(f"Target file does not exist: {target.name}")
        await asyncio.to_thread(target.unlink)
        return {"path": str(target), "deleted": True}

    registry.register(
        ToolSpec(
            tool_id="fs.read",
            input_schema={"path": str},
            output_schema={"path": str, "content": str},
            capabilities=frozenset({ToolCapability.FILESYSTEM_READ}),
            risk_level=ToolRiskLevel.READ_ONLY,
            max_output_bytes=config.max_read_bytes + 4096,
        ),
        _fs_read,
    )
    registry.register(
        ToolSpec(
            tool_id="fs.list",
            input_schema={"path": str},
            output_schema={"path": str, "entries": list},
            capabilities=frozenset({ToolCapability.FILESYSTEM_READ}),
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        _fs_list,
    )
    registry.register(
        ToolSpec(
            tool_id="fs.search",
            input_schema={"path": str, "pattern": str},
            output_schema={"matches": list},
            capabilities=frozenset({ToolCapability.FILESYSTEM_READ}),
            risk_level=ToolRiskLevel.READ_ONLY,
        ),
        _fs_search,
    )
    registry.register(
        ToolSpec(
            tool_id="fs.mkdir",
            input_schema={"path": str},
            output_schema={"path": str, "created": bool},
            capabilities=frozenset({ToolCapability.FILESYSTEM_WRITE}),
            risk_level=ToolRiskLevel.STATE_CHANGING,
        ),
        _fs_mkdir,
    )
    registry.register(
        ToolSpec(
            tool_id="fs.move",
            input_schema={"src": str, "dst": str},
            output_schema={"src": str, "dst": str, "moved": bool},
            capabilities=frozenset({ToolCapability.FILESYSTEM_WRITE}),
            risk_level=ToolRiskLevel.DESTRUCTIVE,
        ),
        _fs_move,
    )
    registry.register(
        ToolSpec(
            tool_id="fs.delete",
            input_schema={"path": str},
            output_schema={"path": str, "deleted": bool},
            capabilities=frozenset({ToolCapability.FILESYSTEM_WRITE}),
            risk_level=ToolRiskLevel.DESTRUCTIVE,
        ),
        _fs_delete,
    )
