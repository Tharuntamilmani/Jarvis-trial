# Changelog

All notable changes to JARVIS are documented in this file.

## [1.0.0]

### Added
- **Core Engine & Lifecycle:** `CoreEngine`, `LifecycleManager`, `ServiceRegistry`, typed `EventBus`, `SingleInstanceLock` (Win32 mutex), and `CrashRecoveryManager` with `--safe-mode` startup (`main.py`, `src/jarvis/core/`).
- **Native PySide6 Desktop Shell:** Unified `ApplicationState` driving three independent windows — **Full-Screen HUD**, **Premium Workspace**, and **Floating Assistant** — with animated orb painting, system tray, and non-destructive hide/close behavior (`src/jarvis/desktop/`).
- **Database & Persistence:** Async SQLAlchemy + `asyncpg` connection pooling with health checks and reconnect backoff, `pgvector` (`vector(1024)` + HNSW indexes), Alembic migrations (`0001_baseline`, `0002_conversation_persistence`, `0003_agent_runtime`), and verified backup/restore with SHA-256 manifests and optional DPAPI encryption (`src/jarvis/database/`, `scripts/backup_db.py`, `scripts/restore_db.py`).
- **Local-First AI & Cloud Isolation:** Unified `LLMRouter` preferring Ollama (`qwen3:8b`), optional per-request consented Groq fallback (`llama-3.3-70b-versatile`) with strict 2-message `CloudPayload` isolation and SHA-256 fingerprint binding (`src/jarvis/intelligence/`).
- **Conversations & Six-Tier Memory:** Durable `ConversationService` with bounded persistence deadlines, plus `MemoryManager` covering working, short-term, long-term semantic, episodic, user preferences, and local opt-in RAG (`nomic-embed-text`) (`src/jarvis/conversations/`, `src/jarvis/memory/`).
- **Autonomous Agents & Scheduler:** Built-in `chat`, `coding`, `productivity`, `research`, and `system` agents, `AgentManager` with bounded execution and recovery, `Planner`, and persistent `TaskScheduler` (`src/jarvis/agents/`).
- **Optional Voice & Vision:** Faster-Whisper STT, Piper TTS, OpenWakeWord, Energy VAD, and barge-in (`src/jarvis/voice/`); opt-in OpenCV camera, DPAPI-encrypted InsightFace recognition, MediaPipe gestures, and kill-switch frame clearing (`src/jarvis/vision/`).
- **Security & Tools:** Win32 DPAPI `CredentialStore` and `BiometricStore`, `PermissionPolicy`, `CommandAllowlist`, single-use `ApprovalService`, `ExecutionSandbox`, `KillSwitchService`, and append-only PostgreSQL + JSONL `AuditService` (`src/jarvis/security/`, `src/jarvis/tools/`).
- **Windows 11 Integration & Diagnostics:** Global hotkeys (`Ctrl+Shift+J`, `Ctrl+Shift+F`, `Ctrl+Alt+Q`), `HKCU\...\Run` startup registration, multi-monitor DPI clamping, `DiagnosticsService` privacy-safe JSON export, and `packaging/build_windows.py` (`src/jarvis/monitoring/`, `packaging/`).

### Verification Status at 1.0.0
- **Automated suite:** `562 passed, 0 failed, 2 skipped` (`tests/integration/test_groq_live.py` opt-in cloud quota tests).
- **Lint & packaging verification:** `ruff check .` passed; `python packaging/build_windows.py --verify-only` passed.
- **Pending:** Full PyInstaller executable bundle (`dist/JARVIS/JARVIS.exe`) and clean-machine Windows 11 smoke testing (PyInstaller not installed in dev environment).
