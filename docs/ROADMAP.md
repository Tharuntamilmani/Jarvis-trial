# JARVIS — Development Roadmap

Rules that apply to every phase:

- One phase at a time; no early implementation of future-phase features.
- Tests written with the code they cover; all tests must pass before a phase
  is considered done.
- Architectural changes are explained before they are made.
- Working modules are never rewritten without need.

## Phase 1 — Native Desktop Foundation ✅ (current)

**Scope:** entry point; configuration; logging; core event bus; service
registry; application lifecycle; PostgreSQL connection (pooling, pre-ping,
recovery); Alembic baseline migration; database health monitoring; PySide6
application shell; HUD window; Workspace window; Floating assistant window;
shared application state; independent window management (hide ≠ close ≠
shutdown); system tray integration; security foundations (credentials via
DPAPI, kill switch, permission policy + allowlist primitives, fail-closed
approval service, append-only audit sink); Ollama/Groq provider **interface
stubs**; automated tests (unit, integration, UI smoke).

**Exit criteria:**

- [x] `python main.py` launches the native app with tray icon.
- [x] All three modes open, hide, close independently; mode switching never
  restarts the engine.
- [x] Shutdown from tray performs graceful engine shutdown.
- [x] Live service status indicators (engine, database, providers, kill
  switch) visible in HUD and Workspace.
- [x] Alembic baseline migration creates the full schema (with fixed
  embedding dimension).
- [x] Kill switch trips and re-arms; approval requests fail closed.
- [x] Unit, integration (DB-gated) and UI tests pass.

## Phase 2 — Intelligence

Unified `intelligence.router`; real Ollama client (streaming, keep-alive) and Groq
client (streaming, timeouts, backoff); provider health via real HTTP checks;
model selection UI; consent gate for cloud; prompt templates; provider
failover. **Exit:** streaming chat works locally and (consented) cloud in
Workspace; privacy policy enforced and tested.

**Progress:**

- [x] Stage 2.1 — provider contracts + typed streaming events.
- [x] Stage 2.2 — real Ollama provider (`qwen3:8b`, mocked + live opt-in).
- [x] Stage 2.3 — real Groq provider + DPAPI credential handling.
- [x] Stage 2.4 — unified router, cloud consent, strict cloud payload
  isolation, retry/health policy.
- [x] Stage 2.5 — chat service + streaming orchestration (`StreamRouted`,
  strict contiguous token sequencing, request-scoped cancellation with
  partial preservation, assistant-state aggregation, concurrency limits,
  kill-switch/close cleanup).
- [x] Stage 2.6 — native Workspace chat UI (`ChatController`, streaming
  bubbles, composer/stop, inline cloud-consent card, provider status,
  in-memory conversation surviving window close/reopen, async Qt wiring
  and deterministic shutdown).
- [x] Stage 2.7 — integration and stabilization: Emergency Stop wiring,
  status hydration/aggregation, HUD `F` and floating quick-command routing,
  provider stream cleanup, deterministic shutdown cancellation, and
  repository-wide lint.

## Phase 3 — Memory

Six-tier memory implementation; pgvector semantic + episodic stores with
configurable-at-migration embedding dimensions; consolidation pass that
*proposes* memories; Memory view with review/approve/pin/delete; dedup by
similarity. **Exit:** assistant recalls approved facts across restarts; no
message becomes permanent without promotion.

## Phase 4 — Agents

`base_agent`, `manager`, `planner`; builtin agents (chat, research, system,
vision placeholders); concurrency limits; agent activity feed in HUD; audit
of runs. **Exit:** multi-step task planned, executed, aggregated and visible.

## Phase 5 — Voice

Audio I/O (sounddevice), VAD; faster-whisper streaming STT; Piper TTS;
OpenWakeWord wake phrase; orb audio reactivity; push-to-talk + wake mode.
**Exit:** hands-free loop wake → speak → hear, with graceful fallbacks.

## Phase 6 — Vision

Camera service (OpenCV); InsightFace embeddings into DPAPI-protected local
biometric store; user recognition → greeting + profile switching; MediaPipe
gestures for mode toggles. **Exit:** recognized-user greeting; biometrics
proven local-only.

## Phase 7 — Tools, Automation & Enforcement

Tool registry with typed schemas and validation; Windows automation tools;
`security/sandbox` execution; permission UI (Workspace → Security); full
approval flow with UI consent cards; DB-backed audit; kill-switch trip on
repeated failures. **Exit:** assistant performs approved desktop actions;
every action audited; deny-by-default verified.

## Phase 8 — Polish & Packaging

PyInstaller build, installer, hotkeys (global summon), performance tuning
(startup < 3 s, first token < 500 ms local), documentation set, backup
scheduling integration. **Exit:** clean-install on Windows 11 machine.
