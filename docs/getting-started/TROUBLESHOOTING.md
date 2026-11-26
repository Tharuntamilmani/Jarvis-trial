# Troubleshooting

Common issues and verified recovery steps on Windows 11.

---

## 1. Database Connection Fails on Startup

**Symptoms:** `DatabaseService` reports `unavailable` or `scripts/setup_db.py` cannot connect.

**Checks:**
1. Verify Docker Desktop is running and the `jarvis-db` container is healthy:
   ```powershell
   docker ps --filter "name=jarvis-db"
   ```
2. If not running, start it:
   ```powershell
   docker compose -f database/docker-compose.yml up -d
   ```
3. Confirm `.env` uses port **`5433`** (the Docker Compose port mapping) rather than `5432`:
   ```ini
   JARVIS_DATABASE_URL=postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis
   ```

---

## 2. Ollama Is Unavailable or Model Missing

**Symptoms:** Local AI provider shows `unavailable` or requests time out.

**Checks:**
1. Confirm Ollama is running on `http://localhost:11434`:
   ```powershell
   ollama list
   ```
2. Ensure the configured chat and embedding models are pulled:
   ```powershell
   ollama pull qwen3:8b
   ollama pull nomic-embed-text
   ```
3. If Ollama is intentionally offline and you want Groq cloud fallback, verify `groq_api_key` is stored in DPAPI (`python scripts/import_groq_key.py`) and approve the inline consent card when prompted.

---

## 3. Application Crashes or Bad Settings on Launch

Run JARVIS in **Safe Mode**:

```powershell
python main.py --safe-mode
```

`--safe-mode` disables optional hardware/agent subsystems so you can inspect Settings, export diagnostics (`DiagnosticsService`), or reset invalid preferences.

---

## 4. Global Hotkey Conflict (`Ctrl+Shift+J`, `Ctrl+Shift+F`, `Ctrl+Alt+Q`)

**Symptoms:** Hotkey registration logs a conflict warning.

**Cause:** Another Windows application already registered that global shortcut via `RegisterHotKey`.

**Resolution:** Close the conflicting application or update the quit hotkey (`JARVIS_HOTKEY_QUIT`) in `.env` / Settings.

---

## 5. Voice or Vision Not Working

- Verify the corresponding extra is installed (`pip install -e ".[voice]"` or `pip install -e ".[vision]"`).
- Remember that **Vision (camera)** is disabled by default on startup and must be explicitly enabled in the UI.
- Use the Diagnostics view (`DiagnosticsService.collect()`) to inspect `check_optional_dependencies()` status and `VoiceDiagnostics` / `VisionDiagnostics` reports.

---

## 6. Second Instance Exits Immediately

JARVIS enforces single-instance execution via a Win32 mutex (`SingleInstanceLock`). Check the Windows system tray for an already-running JARVIS icon before launching again.
