# JARVIS 1.0.0 Documentation

Welcome to the official documentation suite for **JARVIS 1.0.0** — a native Windows 11 AI desktop assistant built with Python 3.12, PySide6, PostgreSQL + `pgvector`, and Ollama.

---

## Documentation Map

### 1. Getting Started
- [Installation](getting-started/INSTALLATION.md) — System prerequisites, Python 3.12, PostgreSQL/Docker, and Ollama setup
- [Setup Guide](getting-started/SETUP_GUIDE.md) — Step-by-step configuration, database initialization, and optional Groq key import
- [First Launch](getting-started/FIRST_LAUNCH.md) — Launching JARVIS, switching between HUD/Workspace/Floating modes, and `--safe-mode`
- [Configuration](getting-started/CONFIGURATION.md) — Complete `JARVIS_` environment variable reference and persisted `app_settings`
- [Dependencies](getting-started/DEPENDENCIES.md) — Core vs. optional (`voice`, `vision`, `telemetry`, `packaging`, `dev`) packages
- [Troubleshooting](getting-started/TROUBLESHOOTING.md) — Common startup, database, Ollama, voice, vision, and hotkey issues

### 2. Architecture
- [System Overview](architecture/SYSTEM_OVERVIEW.md) — High-level layered architecture and strict UI/Core separation
- [Project Structure](architecture/PROJECT_STRUCTURE.md) — Repository layout and module responsibilities
- [Application Lifecycle](architecture/APPLICATION_LIFECYCLE.md) — Startup, service orchestration, hide-vs-close, and graceful shutdown
- [Event System](architecture/EVENT_SYSTEM.md) — Asynchronous `EventBus` and typed `Event` contracts
- [Database Architecture](architecture/DATABASE_ARCHITECTURE.md) — PostgreSQL 16 + `pgvector`, schema overview, and pooling
- [AI Architecture](architecture/AI_ARCHITECTURE.md) — `LLMRouter`, Ollama/Groq providers, streaming, and cloud isolation

### 3. Features
- [Chat & Conversations](features/CHAT_AND_CONVERSATIONS.md) — Streaming chat, durable turn persistence, and reconciliation
- [Memory & RAG](features/MEMORY_AND_RAG.md) — Six-tier memory architecture and local document indexing
- [Autonomous Agents](features/AUTONOMOUS_AGENTS.md) — Built-in agents, `AgentManager`, planning, and `TaskScheduler`
- [Voice Assistant](features/VOICE_ASSISTANT.md) — Faster-Whisper STT, Piper TTS, VAD, wake word, and barge-in
- [Vision & Biometrics](features/VISION_AND_BIOMETRICS.md) — Opt-in camera, DPAPI face recognition, gestures, and kill switch
- [Tools & Automation](features/TOOLS_AND_AUTOMATION.md) — `ToolRegistry`, built-in tools, and sandboxed execution
- [Windows Integration](features/WINDOWS_INTEGRATION.md) — Global hotkeys, tray, `HKCU\...\Run` startup, DPI, and single instance
- [Settings & Diagnostics](features/SETTINGS_AND_DIAGNOSTICS.md) — `PreferencesService`, `DiagnosticsService`, and `StartupProfiler`

### 4. Security & Privacy
- [Security Overview](security/SECURITY_OVERVIEW.md) — Defense-in-depth model and fail-closed defaults
- [Privacy](security/PRIVACY.md) — Local-first data boundaries and biometric/audio/log guarantees
- [Credential Management](security/CREDENTIAL_MANAGEMENT.md) — Win32 DPAPI `CredentialStore` and secret handling
- [Cloud Data Policy](security/CLOUD_DATA_POLICY.md) — Strict 2-message `CloudPayload` and SHA-256 consent binding
- [Permissions & Approvals](security/PERMISSIONS_AND_APPROVALS.md) — Risk levels, `CommandAllowlist`, `ApprovalService`, and `AuditService`

### 5. Database Operations
- [Database Setup](database/DATABASE_SETUP.md) — Docker Compose (`pgvector:pg16` on port `5433`) and `scripts/setup_db.py`
- [Migrations](database/MIGRATIONS.md) — Alembic workflow and migration history (`0001`–`0003`)
- [Backup & Restore](database/BACKUP_AND_RESTORE.md) — `scripts/backup_db.py`, `scripts/restore_db.py`, manifests, and DPAPI encryption
- [Test Database](database/TEST_DATABASE.md) — Isolated `jarvis_test` setup and integration test gating

### 6. Development
- [Development Setup](development/DEVELOPMENT_SETUP.md) — Editable install, dev tools, and local workflow
- [Coding Guidelines](development/CODING_GUIDELINES.md) — Type hints, modularity, UI/Core boundaries, and lint rules
- [Testing](development/TESTING.md) — Running unit, Qt, and PostgreSQL integration tests
- [Adding Features](development/ADDING_FEATURES.md) — Extending tools, agents, events, and views safely

### 7. Deployment & Release
- [Windows Build](deployment/WINDOWS_BUILD.md) — `packaging/jarvis.spec` and `packaging/build_windows.py`
- [Release Process](deployment/RELEASE_PROCESS.md) — Release gates, verification checklist, and current 1.0.0 status
- [Clean Installation](deployment/CLEAN_INSTALLATION.md) — Clean-machine smoke test and uninstall verification

### 8. Visual Assets
- [Screenshots & Diagrams Guide](images/README.md) — Placeholder inventory for UI screenshots and architecture assets
