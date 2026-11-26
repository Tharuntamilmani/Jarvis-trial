# Clean Installation & Smoke Test

Procedure for validating a packaged `dist/JARVIS/JARVIS.exe` build on a separate, clean Windows 11 machine without a local Python development environment.

---

## Smoke Test Checklist

1. **Bundle Hygiene:** Confirm `dist/JARVIS/` does not contain `.env`, API keys, `.bio.bin` biometric files, logs, or development/test databases.
2. **Launch Without Dev Environment:** Run `JARVIS.exe` with no Python virtualenv or repository paths configured.
3. **Database & Ollama Resilience:**
   - Verify initial PostgreSQL connection and schema setup.
   - Verify graceful degradation when Ollama is stopped or missing.
4. **Safe Mode:** Launch `JARVIS.exe --safe-mode` and confirm clean startup.
5. **Single Instance & Tray:** Confirm a second launch exits via Win32 mutex, and tray **Quit** / `Ctrl+Alt+Q` shuts down cleanly.
6. **Uninstall & Cleanup:** If Windows startup was enabled, disable it and confirm only the `JARVIS` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` and JARVIS-owned application files are removed.

> [!NOTE]
> Clean-machine execution of `dist/JARVIS/JARVIS.exe` is pending a full PyInstaller build (see [Release Process](RELEASE_PROCESS.md)).
