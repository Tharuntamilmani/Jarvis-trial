# JARVIS — System Architecture (v2, approved)

JARVIS is a native Windows 11 AI desktop assistant built with Python 3.12 and
PySide6 (Qt Widgets). It is **not** a web application: no browser engine, no
HTML frontend, no Electron/WebView, no web server for internal communication.

## 1. Architectural Principles

1. **Native only** — Qt Widgets, custom `QPainter` painting, QSS styling,
   `QPropertyAnimation` animations.
2. **Core/UI separation** — `src/jarvis/core` and every non-`desktop` module
   must never import PySide6. The desktop layer talks to the core only through
   defined interfaces and the async event bus.
3. **Single async loop** — `qasync` binds the asyncio loop to the Qt loop.
   The core engine is pure asyncio; the UI thread never blocks.
4. **Modularity** — each subsystem is a service with explicit lifecycle,
   registered in the service registry.
5. **Privacy & security by default** — local-first AI, consent gate for cloud,
   explicit approval for sensitive actions, append-only audit log, emergency
   stop. Biometric data never leaves the machine.

## 2. Layered Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     DESKTOP INTERFACE (PySide6)                 │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────┐   │
│  │  HUD Window  │   │ Workspace Window │   │ Floating Window│   │
│  └──────┬───────┘   └────────┬─────────┘   └───────┬────────┘   │
│         └────────────┬───────┴──────────────────────┘            │
│      Event Bridge (core async events → Qt signals)              │
│      Shared AppState + ViewModels (no business logic)           │
│      Window Manager (independent lifecycles, tray)              │
└──────────────────────────────┬──────────────────────────────────┘
                               │ internal interfaces + event bus
┌──────────────────────────────▼──────────────────────────────────┐
│                        CORE ENGINE (no Qt)                      │
│  Event Bus ── Service Registry ── Lifecycle Manager             │
│  ┌───────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │Intelligence│ │  Memory   │ │  Agents  │ │    Perception    │  │
│  │ providers/ │ │ six tiers │ │ planner  │ │ voice / vision   │  │
│  │ (Phase 2+) │ │ (Phase 3+)│ │(Phase 4+)│ │  (Phase 5/6+)    │  │
│  └───────────┘ └───────────┘ └──────────┘ └──────────────────┘  │
│  ┌───────────┐ ┌───────────┐ ┌──────────────────────────────┐   │
│  │ Security  │ │Execution  │ │      Database Layer          │   │
│  │ (Ph.1 fnd)│ │ (Phase 7+)│ │ SQLAlchemy async + pgvector  │   │
│  └───────────┘ └───────────┘ └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
        │                    │                     │
   Ollama (localhost)   PostgreSQL (localhost)   GPU (optional)
```

## 3. AI Provider Architecture

Pluggable providers behind one interface. JARVIS's runtime providers are
configured only through JARVIS's own `config/settings.py` and `.env` — they
are entirely separate from any external coding-tool model configuration.

```
intelligence/providers/
├── base.py            # LLMProvider ABC + request/response/token/caps types
├── errors.py          # ProviderError, ProviderTimeout, ProviderUnavailable, RateLimited
├── registry.py        # provider discovery, health snapshots (routing lives in router.py)
├── ollama_provider.py # local provider (Stage 2.2)
└── groq_provider.py   # cloud provider (Stage 2.3)

intelligence/
├── router.py          # unified local-first router + retry/health policy (Stage 2.4)
├── chat_service.py    # streaming chat orchestration, sequencing, cancellation (Stage 2.5)
├── consent.py         # per-request, fail-closed cloud consent (Stage 2.4)
├── cloud_payload.py   # strict two-message cloud isolation + fingerprint (Stage 2.4)
└── prompts.py         # approved static cloud system prompt
```

```python
class LLMProvider(ABC):
    id: str
    capabilities: ProviderCaps   # streaming, tools, context_size, privacy: local|cloud

    async def health() -> ProviderHealth
    async def list_models() -> list[ModelInfo]
    async def chat(request: ChatRequest) -> ChatResponse
    def stream(request: ChatRequest) -> AsyncIterator[Token]
```

- `intelligence.router` (Phase 2, Stage 2.4) selects by capability,
  availability and privacy policy: **local first; cloud only with explicit,
  per-request consent bound to a payload fingerprint.**
- All vendor failures map to the shared error taxonomy in `errors.py`.
- Availability is surfaced live in the HUD status panel.
- Adding a future provider (e.g. an OpenAI-compatible server) means
  implementing `LLMProvider` and registering it — no router/UI changes.

### 3.1 Unified routing, cloud consent, and payload isolation (Stage 2.4)

- **Local-first:** Ollama is the default provider and receives the full
  session conversation. Cloud is never selected automatically without a
  local attempt (unless a *fresh* hard connection refusal lets the router
  skip that attempt for at most five seconds).
- **Explicit overrides:** `provider="ollama" | "groq"` and `model=...` are
  honored; an explicit choice never switches providers behind the user's back.
- **Consent:** `ConsentManager` denies by default, asks per request, and
  fails closed on rejection, timeout, responder errors, or a missing
  responder. A decision is valid only for its request ID and payload
  fingerprint; there is no persistent or expanded-history consent.
- **Strict cloud isolation:** Groq receives exactly two messages — the
  approved static `JARVIS_SYSTEM_PROMPT` plus the current user message.
  History, memories, files, attachments, tool output, retrieved documents,
  machine details, and hidden state are excluded by construction and
  re-validated (including the serialized HTTP body) immediately before
  dispatch.
- **Retries:** at most two retries per provider per request; only transient
  errors retry; `Retry-After` is honored; backoff is bounded; a single
  120-second budget spans attempts and consent. Cancellation, authentication
  errors, invalid requests, and an active kill switch never retry or fall
  back.
- **Health:** cached health is primarily a UI signal. Actual failures refresh
  it; refresh failures never crash routing; stale health never suppresses a
  real local attempt.
- **Events:** `ProviderSelected`, `CloudFallbackProposed`, and
  `CloudConsentDecided` travel over the existing event bus; no new bus or
  service registry exists.

### 3.2 Streaming chat orchestration (Stage 2.5)

`intelligence.chat_service.ChatService` is the core layer that turns
router streaming into a public, cancellable chat stream. It stays fully
UI-agnostic and PySide6-free.

- **Routing metadata:** `LLMRouter.stream_chat()` yields exactly one
  `StreamRouted` item (request ID, selected provider, model, privacy,
  consent status) immediately before that provider's `StreamStart`.
- **Commitment rule:** automatic local→cloud fallback is possible only
  while the local attempt has not produced a `StreamStart`. Once a stream
  starts, the provider is committed: later failures become
  `ChatStreamFailed` and never switch providers. Cloud fallback always
  repeats the Stage 2.4 consent, fingerprint, and serialized-body checks.
- **One public sequence:** `ChatService` owns the public token sequence
  (0, 1, 2, …) and independently validates provider sequence numbers.
  Duplicate, backward, skipped or out-of-order provider tokens are
  dropped. Empty chunks are ignored. Mismatched request IDs and events
  after the terminal event are ignored.
- **Partial preservation:** `ChatStreamCompleted.content` is exactly the
  concatenation of published `ChatToken` events;
  `ChatStreamCancelled.partial_content` and
  `ChatStreamFailed.partial_content` follow the same rule.
- **Cancellation:** `cancel(request_id)` cancels only that request's
  owning task, closes the provider stream, preserves partial output,
  emits exactly one `ChatStreamCancelled`, and never retries, falls back,
  or starts another provider request. Cancelling while consent is
  pending removes the pending decision, so a late approval cannot
  dispatch Groq.
- **Concurrency:** default limit is one active generation with a `reject`
  policy; `queue` is available and the limit is configurable. A queued
  request does not affect assistant state until it acquires a slot.
- **Assistant state:** `AssistantStateChanged` reports the aggregate of
  `speaking > thinking > idle`; it is published only when the aggregate
  changes.
- **Kill switch:** active tasks are registered as killable; activation
  cancels active and queued requests, preserves partials, and
  `ChatService.close()` cancels and awaits every task idempotently.
- **Events:** `ChatStreamStarted`, `ChatToken`, `ChatStreamCompleted`,
  `ChatStreamFailed`, `ChatStreamCancelled`, and
  `AssistantStateChanged` use the existing event bus. Exactly one
  terminal event is published per request.


### 3.3 Native Workspace chat (Stage 2.6)

`desktop.viewmodels.chat_controller.ChatController` is the app-owned bridge
between the core chat service and every chat surface. It is created once by
`DesktopApplication`, so conversation state survives Workspace close/reopen
and tray-only operation.

- **Data flow:** `ChatService` → typed events → `EventBus` → `EventBridge`
  → `AppState` signals → `ChatController` → `ChatView`. The UI never calls a
  provider or the router directly.
- **Requests:** the controller generates `uuid4().hex` before dispatch and
  passes it through the existing `start_chat(..., request_id=...)`
  parameter. All incoming events are filtered by that ID; duplicate,
  out-of-order, and post-terminal events are ignored.
- **Local history:** the in-memory conversation (user + assistant) is sent
  to Ollama, bounded to 20 messages / ~12,000 characters, always keeping the
  newest user message and never starting with an assistant message.
- **Cloud boundary unchanged:** the router still builds exactly two cloud
  messages (approved system prompt + current user message). The local
  history never expands cloud scope.
- **Consent:** `ChatController` installs the `ConsentManager` responder and
  shows an inline, non-modal card with the exact payload. Approve is bound
  to request ID + fingerprint; reject, close, Escape, cancellation, kill
  switch, and shutdown all deny. Late decisions are rejected.
- **Cancellation:** Stop is idempotent and request-scoped. A Stop clicked
  before `start_chat` returns is recorded and cancelled exactly once after
  registration. Partial content is preserved for cancelled/failed streams.
- **Provider status:** `CoreEngine` exposes an additive, secret-free
  `ProviderStatus` facade. Initial UI attach uses cached data only; the
  first Workspace show performs one real health refresh, the sidebar has a
  manual Refresh button, health events update the UI, and there is no
  background polling.
- **Shutdown order:** set closing → deny/lock pending consent → cancel UI
  dispatch → cancel the active chat request → clear pending UI state →
  detach the responder → shut down the core → close windows → quit.

## 4. Memory Architecture — Six Explicit Tiers

| Tier | Module | Store | Lifetime | Purpose |
|---|---|---|---|---|
| 1 Working | `memory/working_memory.py` | In-process | Current task | Agent scratchpad |
| 2 Short-term | `memory/short_term.py` | In-process ring buffer | Current conversation | LLM context window |
| 3 Conversation | `memory/conversation.py` | Postgres `messages` | Forever | Searchable history |
| 4 Semantic | `memory/semantic.py` | Postgres `memories` + pgvector | Until deleted | Facts & knowledge |
| 5 Episodic | `memory/episodic.py` | Postgres `episodes` + pgvector + time | Until deleted | Dated events |
| 6 Preferences | `memory/preferences.py` | Postgres `user_preferences` | Forever | Settings & habits |

**No automatic permanence.** Messages are never auto-promoted. Promotion to
semantic/episodic memory happens only when:

1. the user explicitly asks ("remember this") or pins content in the UI; or
2. a background consolidation pass *proposes* candidates with confidence
   scores that the user approves in the Workspace Memory view (or has enabled
   threshold-gated auto-accept in Settings).

Deduplication by vector similarity prevents memory rot. (Implemented Phase 3.)

## 5. Security Architecture

`src/jarvis/security/` — Phase 1 delivers the **foundations** (interfaces,
policies, primitives); the full enforcement layers arrive with tools (Phase 7).

```
security/
├── credentials.py   # Windows DPAPI-protected secret store (Phase 1, real)
├── kill_switch.py   # EmergencyStop service (Phase 1, real)
├── permissions.py   # RiskLevel + PermissionPolicy, default-deny dangerous (Phase 1)
├── allowlist.py     # CommandAllowlist exact/pattern matching (Phase 1)
├── audit.py         # append-only JSONL audit sink (Phase 1; DB-backed in Phase 7)
├── approval.py      # ApprovalService: request flow, fail-closed auto-deny (Phase 1)
└── sandbox.py       # executes approved actions (Phase 7)
```

- **Explicit approval:** sensitive actions raise `ApprovalRequest` events;
  a UI responder shows a consent card; no responder ⇒ auto-**deny**
  (fail-closed).
- **Permission policies:** tools declare a risk class
  (`safe | elevated | dangerous`) and required capability. Default deny for
  `dangerous`. User-configurable in Workspace → Security (Phase 7 UI).
- **Command allowlists:** shell commands execute only when matching an
  allowlist entry; unknown/generated commands always require approval.
- **Audit logging:** every decision and action → append-only audit sink.
- **Emergency stop (kill switch):** tray entry + hotkey. Cancels registered
  tasks, halts the execution queue, blocks new sensitive actions until
  manually re-armed. Repeated tool failures also trip it (Phase 7).
- **Credential storage:** secrets (API keys) live in Windows DPAPI-protected
  stores; `.env` holds non-secret config only. Keys never appear in logs,
  prompts, or the database.

## 6. Desktop Window Management

Three modes, one engine, one shared state.

- **Shared state:** every window binds to the same `AppState` viewmodels fed
  by the `EventBridge`. Mode choice never forks state.
- **Independent lifecycles:** `desktop/window_manager.py` manages each window
  separately: create on demand, show, hide, close — each window can be
  opened, hidden or closed without touching the others.
- **Hiding ≠ closing ≠ shutdown:**
  - **Hide** — window becomes invisible; state preserved; instantly restorable.
  - **Close** — window is closed and removed from the active set; the core
    engine and other windows are unaffected.
  - **Shutdown** — explicit quit (tray → Quit): engine graceful shutdown,
    audit flush, DB pool close, tray removed, process exits.
- **Mode switching = window visibility changes only.** The AI engine is never
  restarted or reloaded; streams, voice and timers continue even with zero
  windows open (tray-only mode). Closing all windows minimizes to tray;
  quitting is possible only from the tray.

## 7. Database Architecture

PostgreSQL 16 + `pgvector`, SQLAlchemy 2.0 async (`asyncpg`), **Alembic**
migrations for all schema changes (no `create_all` in production paths).

| Table | Purpose | Notable columns |
|---|---|---|
| `users` | Profiles (non-biometric) | `id`, `name`, `preferences`, `created_at` |
| `conversations` | Sessions | `id`, `user_id`, `title`, `mode`, `created_at` |
| `messages` | Chat history | `id`, `conversation_id`, `role`, `content`, `tokens` |
| `memories` | Semantic memory | `content`, `embedding VECTOR(dim)`, `importance` |
| `episodes` | Episodic memory | `content`, `embedding VECTOR(dim)`, `occurred_at` |
| `user_preferences` | Tier-6 preferences | `user_id`, `key`, `value jsonb` |
| `agent_runs` | Agent executions | `agent`, `input`, `output`, `status`, `duration_ms` |
| `audit_log` | Append-only execution audit | `action`, `tool`, `args`, `approved`, `result` |
| `app_settings` | App config overrides | `key`, `value jsonb` |

- **Embedding dimensions are fixed per database.** The dimension is read from
  configuration at migration time and baked into the schema; changing it
  requires a **new explicit Alembic migration** (plus a re-embed pass). There
  is no runtime dimension switching.
- **Connection recovery:** `pool_pre_ping=True`, reconnect with exponential
  backoff, degraded-status event instead of crash.
- **Backups:** `scripts/backup_db.py` (`pg_dump` wrapper, configurable
  retention) — foundation in Phase 1, scheduled task integration documented.
- **Biometric data:** face embeddings are stored only in DPAPI-encrypted
  local storage (`security/biometrics.py`, Phase 6) — never in Postgres,
  never synced, never logged. Camera frames are processed in memory only.
- Retrieval: `ORDER BY embedding <=> :query LIMIT k` + recency/importance
  weighting (Phase 3), HNSW index on vector columns.

## 8. Data Flow (target system)

```
Voice/Text → Event Bus → Agent Planner → LLM Router (local first,
consent-gated cloud) → Memory retrieval (pgvector) → Tool/Execution
(approval gate + allowlist) → Response → Event Bus → all visible windows
```

## 9. Prohibited (per project rules)

Next.js, React, HTML frontend, browser dashboards, FastAPI, Flask, Electron,
WebView-based interfaces, and any web server as the internal communication
layer. Internal communication is the asyncio event bus + Qt signal bridge.
