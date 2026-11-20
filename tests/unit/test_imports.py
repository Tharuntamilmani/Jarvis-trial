"""Enforce the core/UI separation rule via static analysis.

Every module outside ``jarvis.desktop`` must not import PySide6.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "jarvis"
FORBIDDEN = {"PySide6", "PyQt6", "PyQt5"}


def _imports_pyside(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN:
                hits.append(node.module)
    return hits


def test_no_qt_imports_outside_desktop() -> None:
    violations: list[str] = []
    for py_file in SRC.rglob("*.py"):
        rel = py_file.relative_to(SRC)
        if rel.parts[0] == "desktop":
            continue  # the only allowed consumer
        if hits := _imports_pyside(py_file):
            violations.append(f"{rel}: {hits}")
    assert not violations, f"Core modules import PySide6: {violations}"


def test_desktop_owns_ui() -> None:
    windows = list((SRC / "desktop" / "windows").glob("*.py"))
    names = {p.stem for p in windows}
    assert {"hud_window", "workspace_window", "floating_window"} <= names
