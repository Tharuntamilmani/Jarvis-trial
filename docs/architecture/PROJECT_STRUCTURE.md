# Project Structure

Top-level layout of the JARVIS 1.0.0 repository:

```text
Jarvis/
├── main.py                         # Entry point, CLI (--safe-mode), SingleInstanceLock, qasync loop
├── pyproject.toml                  # Python >=3.12 dependencies, optional extras, pytest config
├── alembic.ini                     # Alembic migration configuration
├── .env.example                    # Safe environment configuration template
├── AGENTS.md                       # Master development & architecture rules
├── database/
│   ├── docker-compose.yml          # pgvector/pgvector:pg16 container (port 5433)
│   └── migrations/versions/        # 0001_baseline, 0002_conversation_persistence, 0003_agent_runtime
├── packaging/
│   ├── jarvis.spec                 # PyInstaller bundle specification
│   └── build_windows.py            # Windows release build & --verify-only script
├── scripts/
│   ├── setup_db.py                 # Create DB + run Alembic upgrade head
│   ├── backup_db.py                # pg_dump backup with SHA-256 manifest & optional DPAPI encryption
│   ├── restore_db.py               # Verified backup restore (--dry-run, --confirm)
│   └── import_groq_key.py          # One-time import of GROQ_API_KEY into Win32 DPAPI
├── src/jarvis/
│   ├── core/                       # CoreEngine, EventBus, events, LifecycleManager, ServiceRegistry, recovery
│   ├── config/                     # Settings (JARVIS_ env) & PreferencesService (app_settings)
│   ├── database/                   # SQLAlchemy models, async DatabaseService, backup/restore
│   ├── desktop/                    # PySide6 ApplicationState, WindowManager, HUD/Workspace/Floating, tray, hotkeys
│   ├── intelligence/               # LLMRouter, OllamaClient, GroqClient, ConsentManager, CloudPayload, ChatService
│   ├── conversations/              # ConversationService, repository, durable turn reconciliation
│   ├── memory/                     # MemoryManager (6 tiers), LocalDocumentIndexer (RAG), embeddings
│   ├── agents/                     # AgentManager, Planner, TaskScheduler, built-in agents
│   ├── security/                   # DPAPI CredentialStore & BiometricStore, ApprovalService, Sandbox, AuditService
│   ├── tools/                      # ToolRegistry, filesystem, system, clipboard, telemetry tools
│   ├── voice/                      # VoiceService, FasterWhisperSTT, PiperTTSEngine, EnergyVAD, OpenWakeWord
│   ├── vision/                     # VisionService, OpenCV camera, InsightFace, MediaPipe gestures
│   ├── monitoring/                 # Logging setup, DiagnosticsService, StartupProfiler
│   ├── perception/                 # Perception integration package
│   ├── execution/                  # Execution integration package
│   └── integrations/               # External/OS integration package
├── tests/                          # Unit, Qt UI, and PostgreSQL integration tests
└── docs/                           # Complete JARVIS 1.0.0 documentation suite
```

See [Database Architecture](DATABASE_ARCHITECTURE.md) and [AI Architecture](AI_ARCHITECTURE.md) for subsystem details.
