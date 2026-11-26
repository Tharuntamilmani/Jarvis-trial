# Release Process

Release readiness for JARVIS 1.0.0 follows four gates: database integration, hardware/OS acceptance, clean-machine packaging, and security/lint verification.

---

## 1.0.0 Release Gate Status

| Gate | Status | Evidence |
| :--- | :--- | :--- |
| **PostgreSQL `jarvis_test` Integration** | **Passed** | `562 passed, 0 failed, 2 skipped` (`tests/integration/test_groq_live.py` opt-in cloud quota tests skipped unless `JARVIS_RUN_GROQ_LIVE=1`) |
| **Static Lint** | **Passed** | `ruff check .` passed with 0 errors |
| **Windows Packaging Dry-Run** | **Passed** | `python packaging/build_windows.py --verify-only` passed |
| **Hardware & Windows 11 Probes** | **Automated Probes Passed; Manual Checklist Pending** | Live probes verified OS (`Windows-11-10.0.26200-SP0`), display (`1536x864` DPR `1.25`), 28 audio devices, 1 camera (`is_open: False` after probe), Win32 mutex, `RegisterHotKey`, and `HKCU\...\Run` |
| **Full PyInstaller `.exe` & Clean-Machine Smoke** | **Pending** | `PyInstaller` not installed in dev environment; `dist/JARVIS/JARVIS.exe` not yet built or tested on a separate clean Windows 11 machine |

---

## Release Checklist Commands (PowerShell)

```powershell
# 1. Lint
ruff check .

# 2. Full test suite against isolated jarvis_test
$env:JARVIS_TEST_DATABASE_URL = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"
python -m pytest tests -v

# 3. Packaging verification & build
python packaging/build_windows.py --verify-only
python packaging/build_windows.py
```
