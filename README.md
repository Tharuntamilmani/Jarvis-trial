# JARVIS — Native Windows 11 AI Desktop Assistant

**Version:** `1.0.0` · **Platform:** Windows 11 · **Runtime:** Python 3.12 · **UI:** PySide6 · **Database:** PostgreSQL + pgvector · **Local AI:** Ollama

JARVIS is a native, local-first Windows 11 desktop AI assistant built with PySide6 (Qt Widgets, QSS, `QPropertyAnimation`, and `qasync`). It is **not** a web application or Electron/WebView wrapper: all three desktop modes (**Full-Screen HUD**, **Premium Workspace**, and **Floating Assistant**) run inside a single Python process over an internal asynchronous `EventBus` and `CoreEngine`.

> [!NOTE]
> **1.0.0 Release Verification Status**
> - **Automated test suite:** `562 passed, 0 failed, 2 skipped` (`tests/integration/test_groq_live.py` is opt-in and requires `JARVIS_RUN_GROQ_LIVE=1` plus a DPAPI-stored `groq_api_key`).
> - **Static lint & packaging dry-run:** `ruff check .` passed; `python packaging/build_windows.py --verify-only` passed.
> - **Pending clean-machine / full bundle evidence:** Full PyInstaller `.exe` build (`dist/JARVIS/JARVIS.exe`) and separate clean-machine Windows 11 smoke testing have not yet been executed (`PyInstaller` not installed in the dev environment). See [Release Process](docs/deployment/RELEASE_PROCESS.md).

---

## Key Capabilities

- **Three Unified Desktop Modes:** Full-Screen HUD, Premium Workspace (chat, conversations, memory, agents, settings, security, diagnostics), and always-on-top Floating Assistant — all sharing one `CoreEngine` and `ApplicationState`.
- **Local-First AI & Strict Cloud Isolation:** Ollama (`qwen3:8b` default) is primary. Optional Groq (`llama-3.3-70b-versatile` default) fallback is **fail-closed**, requires per-request user consent bound by SHA-256 fingerprint, and sends **only** the static system prompt + current user message (`2` messages total — never conversation history, memories, files, or tool outputs).
- **Six-Tier Memory & Local RAG:** Working memory, short-term memory, PostgreSQL + `pgvector` long-term semantic memory (`vector(1024)` with HNSW indexes), episodic summaries, user preferences, and opt-in local document RAG (`nomic-embed-text`).
- **Autonomous Agents & Scheduler:** Built-in agents (`chat`, `coding`, `productivity`, `research`, `system`) with bounded runs persisted in `agent_runs`, multi-step planning, and a persistent `TaskScheduler`.
- **Optional Voice & Vision (Disabled by Default):** Local Faster-Whisper STT, Piper TTS, OpenWakeWord, VAD, and barge-in; opt-in OpenCV camera, Win32 DPAPI-encrypted InsightFace embeddings, MediaPipe gestures, and instant kill-switch clearing.
- **Security & Windows 11 Integration:** Win32 DPAPI credential & biometric storage, single-use SHA-256-bound execution approvals, command allowlist, append-only PostgreSQL + JSONL audit logs, `Ctrl+Shift+J` / `Ctrl+Shift+F` / `Ctrl+Alt+Q` global hotkeys, system tray, single-instance Win32 mutex, and optional `HKCU\...\Run` startup registration.

---

## Quick Start (Windows 11 PowerShell)

```powershell
# 1. Start PostgreSQL 16 + pgvector container (published on localhost:5433)
docker compose -f database/docker-compose.yml up -d

# 2. Configure environment (copy template; set port 5433 if using Docker Compose)
Copy-Item .env.example .env

# 3. Install core dependencies (Python 3.12+)
python -m pip install -e ".[dev]"

# 4. Create database and apply Alembic migrations (head: 0003_agent_runtime)
python scripts/setup_db.py

# 5. Launch JARVIS
python main.py
```

For safe recovery startup without optional hardware/agent modules:

```powershell
python main.py --safe-mode
```

---

## Documentation Index

Start with [docs/README.md](docs/README.md) for the full table of contents, or jump directly to:

| Section | Highlights |
| :--- | :--- |
| **Getting Started** | [Installation](docs/getting-started/INSTALLATION.md) · [Setup Guide](docs/getting-started/SETUP_GUIDE.md) · [First Launch](docs/getting-started/FIRST_LAUNCH.md) · [Configuration](docs/getting-started/CONFIGURATION.md) · [Dependencies](docs/getting-started/DEPENDENCIES.md) · [Troubleshooting](docs/getting-started/TROUBLESHOOTING.md) |
| **Architecture** | [System Overview](docs/architecture/SYSTEM_OVERVIEW.md) · [Project Structure](docs/architecture/PROJECT_STRUCTURE.md) · [Lifecycle](docs/architecture/APPLICATION_LIFECYCLE.md) · [Event System](docs/architecture/EVENT_SYSTEM.md) · [Database Architecture](docs/architecture/DATABASE_ARCHITECTURE.md) · [AI Architecture](docs/architecture/AI_ARCHITECTURE.md) |
| **Features** | [Chat & Conversations](docs/features/CHAT_AND_CONVERSATIONS.md) · [Memory & RAG](docs/features/MEMORY_AND_RAG.md) · [Autonomous Agents](docs/features/AUTONOMOUS_AGENTS.md) · [Voice Assistant](docs/features/VOICE_ASSISTANT.md) · [Vision & Biometrics](docs/features/VISION_AND_BIOMETRICS.md) · [Tools & Automation](docs/features/TOOLS_AND_AUTOMATION.md) · [Windows Integration](docs/features/WINDOWS_INTEGRATION.md) · [Settings & Diagnostics](docs/features/SETTINGS_AND_DIAGNOSTICS.md) |
| **Security & Privacy** | [Security Overview](docs/security/SECURITY_OVERVIEW.md) · [Privacy](docs/security/PRIVACY.md) · [Credential Management](docs/security/CREDENTIAL_MANAGEMENT.md) · [Cloud Data Policy](docs/security/CLOUD_DATA_POLICY.md) · [Permissions & Approvals](docs/security/PERMISSIONS_AND_APPROVALS.md) |
| **Database** | [Database Setup](docs/database/DATABASE_SETUP.md) · [Migrations](docs/database/MIGRATIONS.md) · [Backup & Restore](docs/database/BACKUP_AND_RESTORE.md) · [Test Database](docs/database/TEST_DATABASE.md) |
| **Development** | [Development Setup](docs/development/DEVELOPMENT_SETUP.md) · [Coding Guidelines](docs/development/CODING_GUIDELINES.md) · [Testing](docs/development/TESTING.md) · [Adding Features](docs/development/ADDING_FEATURES.md) |
| **Deployment** | [Windows Build](docs/deployment/WINDOWS_BUILD.md) · [Release Process](docs/deployment/RELEASE_PROCESS.md) · [Clean Installation](docs/deployment/CLEAN_INSTALLATION.md) |

---

## Project Governance & Policies

- [CHANGELOG.md](CHANGELOG.md) — Version `1.0.0` release notes and verification record
- [SECURITY.md](SECURITY.md) — Threat model, credential storage, and vulnerability reporting
- [CONTRIBUTING.md](CONTRIBUTING.md) — Architecture rules, testing requirements, and contribution workflow
- [AGENTS.md](AGENTS.md) — Master development instructions (binding)
- **License:** *Needs verification* (no `LICENSE` file is currently present in the repository root).
