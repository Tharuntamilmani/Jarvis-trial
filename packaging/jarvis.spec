# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for JARVIS Windows 11 native desktop app (Phase 8.4 / 9.5).

Strictly excludes .env, secrets/, biometrics/, logs/, database/backups/, and tests/.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821

block_cipher = None

# Exclude dev/test tools; never exclude stdlib 'secrets'.
EXCLUDED_MODULES = [
    "pytest",
    "pytest_asyncio",
    "pytest_qt",
    "ruff",
]

HIDDEN_IMPORTS = [
    "qasync",
    "asyncpg",
    "psycopg2",
    "sqlalchemy.dialects.postgresql",
    "pgvector",
    "jarvis.desktop.application",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],  # Never bundle .env, secrets/, biometrics/, or backups/
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="JARVIS",
)
