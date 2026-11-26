# Windows Build (`PyInstaller`)

JARVIS packages into a standalone Windows 11 directory bundle via `packaging/jarvis.spec` and `packaging/build_windows.py`.

---

## Prerequisites

Install the packaging extra:

```powershell
python -m pip install -e ".[packaging]"
```

---

## Dry-Run Packaging Verification

Verify the build specification, entry points, and exclusion rules (no `.env`, secrets, biometric files, or dev/test databases) without running PyInstaller:

```powershell
python packaging/build_windows.py --verify-only
```

---

## Building `dist/JARVIS/JARVIS.exe`

```powershell
python packaging/build_windows.py
```

Expected output artifact:

```text
dist/
└── JARVIS/
    ├── JARVIS.exe
    └── ...
```

> [!NOTE]
> `python packaging/build_windows.py --verify-only` has been verified in the development environment. A full `PyInstaller` executable build has not yet been run because `PyInstaller` is not installed in the current environment (`No module named PyInstaller`).
