# JARVIS — Project Structure & Module Responsibilities

## Folder Layout

```
Jarvis/
├── main.py                        # Entry point: config → logging → qasync → UI
├── pyproject.toml                 # Project metadata, dependencies, tool config
├── .env.example                   # Non-secret configuration template
├── alembic.ini                    # Alembic configuration
├── AGENTS.md                      # Master development instructions (preserved)
├── database/
│   ├── docker-compose.yml         # PostgreSQL 16 + pgvector for development
│   ├── init/01_extensions.sql     # CREATE EXTENSION vector;
│   └── migrations/                # Alembic environment + versions
│       ├── env.py
│       ├── script.py.mako
│       └── versions/
├── docs/
│   ├── ARCHITECTURE.md            # Full architecture blueprint (v2)
│   ├── PROJECT_STRUCTURE.md       # This file
│   ├── ROADMAP.md                 # Phases with exit criteria
│   └── TECH_STACK.md              # Stack, versions, rationale
├── scripts/
│   ├── setup_db.py                # Create DB, extensions, run migrations
│   └── backup_db.py               # pg_dump backup wrapper (foundation)
├── src/jarvis/
│   ├── __init__.py
│   ├── application.py             # Core bootstrap (pre-Qt)
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py            # pydantic-settings; env-first (JARVIS_ prefix)
│   ├── core/                      # ⛔ NEVER imports PySide6
│   │   ├── __init__.py
│   │   ├── events.py              # Typed event dataclasses (the event contract)
│   │   ├── event_bus.py           # Async publish/subscribe
│   │   ├── service_registry.py    # Lazy service registration + resolution
│   │   ├── lifecycle.py           # Ordered startup, health checks, shutdown
│   │   ├── engine.py              # Wires core services; owns lifecycle
│   │   └── exceptions.py          # Core exception hierarchy
│   ├── security/                  # Foundations in Phase 1
│   │   ├── __init__.py
│   │   ├── credentials.py         # Windows DPAPI secret store
│   │   ├── kill_switch.py         # EmergencyStop service
│   │   ├── permissions.py         # RiskLevel, PermissionPolicy (default-deny)
│   │   ├── allowlist.py           # CommandAllowlist
│   │   ├── approval.py            # ApprovalService (fail-closed)
│   │   └── audit.py               # Append-only JSONL audit sink
│   ├── intelligence/
│   │   ├── __init__.py
│   │   ├── prompts.py             # Approved static cloud system prompt
│   │   ├── cloud_payload.py       # Strict two-message cloud isolation + fingerprint
│   │   ├── consent.py             # Per-request, fail-closed cloud consent
│   │   ├── router.py              # Unified local-first router (Stage 2.4)
│   │   ├── chat_service.py        # Streaming orchestration + assistant state (Stage 2.5)
│   │   └── providers/             # Provider contract + real Ollama/Groq
│   │       ├── __init__.py
│   │       ├── base.py            # LLMProvider ABC + shared types
│   │       ├── errors.py          # Provider error taxonomy
│   │       ├── registry.py        # ProviderRegistry + health snapshots
│   │       ├── ollama_provider.py # Real local provider (Stage 2.2)
│   │       └── groq_provider.py   # Real cloud provider (Stage 2.3)
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py          # Async engine, pooling, recovery, health
│   │   ├── types.py               # VECTOR UserDefinedType for pgvector
│   │   └── models.py              # ORM models (all tables)
│   ├── monitoring/
│   │   ├── __init__.py
│   │   └── logging.py             # Rotating file + console logging
│   ├── desktop/                   # ✅ The ONLY place PySide6 is imported
│   │   ├── __init__.py
│   │   ├── application.py         # QApplication + qasync loop + assembly
│   │   ├── window_manager.py      # Independent lifecycles: show/hide/close/shutdown
│   │   ├── tray.py                # System tray icon + menu (incl. kill switch)
│   │   ├── adapters/
│   │   │   ├── __init__.py
│   │   │   └── event_bridge.py    # Core events → Qt signals → AppState
│   │   ├── viewmodels/
│   │   │   ├── __init__.py
│   │   │   ├── app_state.py       # Shared state (single source for all windows)
│   │   │   ├── chat_models.py     # Conversation models + local history truncation
│   │   │   └── chat_controller.py # App-owned chat lifecycle/consent/status
│   │   ├── views/
│   │   │   ├── __init__.py
│   │   │   ├── chat_view.py       # Welcome, bubbles, streaming, composer
│   │   │   ├── message_bubble.py  # Plain-text selectable message bubble
│   │   │   └── consent_overlay.py # Inline cloud-consent card
│   │   ├── theme/
│   │   │   ├── __init__.py
│   │   │   └── theme.py           # Palette constants + QSS builder
│   │   ├── common/
│   │   │   ├── __init__.py
│   │   │   ├── status_chip.py     # Service status indicator widget
│   │   │   └── orb.py             # Animated AI orb (pulsing, state-aware)
│   │   └── windows/
│   │       ├── __init__.py
│   │       ├── hud_window.py      # Full-screen futuristic HUD
│   │       ├── workspace_window.py# Premium workspace (nav + placeholder views)
│   │       └── floating_window.py # Always-on-top compact assistant
│   └── tests-adjacent code lives in /tests (not in package)
├── tests/
│   ├── conftest.py
│   ├── unit/                      # Core, config, security, providers, models
│   ├── integration/               # DB (skipped without a live PostgreSQL)
│   └── ui/                        # pytest-qt smoke tests for all three windows
├── logs/                          # Rotating logs + audit.log (runtime, git-ignored)
└── .env                           # Local config (git-ignored)
```

## Module Responsibilities

| Module | Owns | Never does |
|---|---|---|
| `config` | Typed settings from env/`.env`, validation | I/O beyond parsing, UI |
| `monitoring.logging` | Logger setup, rotating files, audit file path | Business logic |
| `core.events` | The typed event contract between modules | Anything else |
| `core.event_bus` | Async pub/sub, subscriber isolation (handler errors logged, never crash bus) | Business logic |
| `core.service_registry` | Registration, lazy async resolution, lookup | Lifecycle ordering (that is `lifecycle`) |
| `core.lifecycle` | Ordered startup/shutdown, per-service health checks, kill-switch coordination | Service internals |
| `core.engine` | Assembles core services, exposes facade used by the UI | PySide6, window management |
| `security.credentials` | DPAPI encrypt/decrypt, secret read/write/delete | Anything else |
| `security.kill_switch` | Trigger/re-arm, task cancellation registry, event broadcast | Executing tools |
| `security.permissions` | Risk classes, policy evaluation (default-deny dangerous) | Execution |
| `security.allowlist` | Command matching (exact + patterns) | Execution |
| `security.approval` | Approval request lifecycle, fail-closed auto-deny | Execution |
| `security.audit` | Append-only audit records (JSONL in Phase 1) | Mutation of history |
| `intelligence.providers.base` | Provider contract + shared data types | Vendor specifics |
| `intelligence.providers.registry` | Registration, health snapshots | Routing |
| `intelligence.providers.ollama/groq` | Vendor specifics (Phase 2) | UI, memory |
| `intelligence.router` | Local-first selection, bounded retries, health fast-path, consent gate enforcement, `ProviderSelected` events, consent-gated `StreamRouted` streaming | Provider internals, UI, token publication |
| `intelligence.chat_service` | Request IDs, router streaming orchestration, strict token sequencing, cancellation/partials, assistant state, concurrency limits | Provider specifics, UI, persistence |
| `intelligence.consent` | Per-request consent lifecycle, request/fingerprint binding, fail-closed denial | Persistence, UI rendering |
| `intelligence.cloud_payload` | Two-message cloud isolation, payload fingerprint, serialized-body validation | Transmission, local history redaction |
| `intelligence.prompts` | The only approved cloud system prompt | User-specific context |
| `database.connection` | Engine, pooling, pre-ping, reconnect w/ backoff, health check | Query logic |
| `database.models` | ORM table definitions | Data access patterns |
| `desktop.application` | QApplication, qasync, assembly, startup order | Business logic |
| `desktop.window_manager` | Window creation/show/hide/close; shutdown sequence; tray anchoring | Core logic |
| `desktop.adapters.event_bridge` | Core events → Qt signals (thread-safe) | UI rendering |
| `desktop.viewmodels.app_state` | Shared observable state for all windows | Business logic |
| `desktop.viewmodels.chat_controller` | In-memory conversation, request IDs, sequencing, cancel/consent, provider status | Provider access, persistence |
| `desktop.viewmodels.chat_models` | Conversation dataclasses + bounded local history | Cloud payload construction |
| `desktop.views.*` | Chat rendering, composer, inline consent card | Core engine access (via controller only) |
| `desktop.windows.*` | Rendering, input, per-window behavior | Core engine access (via bridge only) |
| `scripts.*` | DB setup, backups | Application runtime |

## Import Rules (enforced by review, tested in `tests/unit/test_imports.py`)

1. `src/jarvis/core`, `security`, `intelligence`, `database`, `config`,
   `monitoring` must not import PySide6.
2. Only `desktop` and `main.py` may import PySide6.
3. The core communicates with the UI only via the event bus + facade methods;
   the UI communicates back via facade methods, never by touching internals.
