# System Overview

JARVIS 1.0.0 is a **native Windows 11 desktop application** built in Python 3.12 with **PySide6**, **qasync**, **PostgreSQL + pgvector**, and **Ollama**. There is no web server, no HTML frontend, and no WebView/Electron shell.

---

## Layered Architecture

```mermaid
flowchart TB
    subgraph UI["Desktop Layer (PySide6 + qasync)"]
        HUD["Full-Screen HUD"]
        WS["Premium Workspace"]
        FL["Floating Assistant"]
        TRAY["System Tray & Global Hotkeys"]
        STATE["Shared ApplicationState & WindowManager"]
        HUD --- STATE
        WS --- STATE
        FL --- STATE
        TRAY --- STATE
    end

    subgraph CORE["Core Engine (UI-Agnostic Python)"]
        BUS["Async EventBus"]
        REG["ServiceRegistry"]
        LIFE["LifecycleManager"]
        REC["CrashRecoveryManager"]
    end

    subgraph SERVICES["Domain Services"]
        AI["Intelligence (LLMRouter, Ollama, Groq, Consent)"]
        MEM["Memory & RAG (6 Tiers, pgvector)"]
        CONV["Conversations (Durable Turn Control)"]
        AGT["Agents & TaskScheduler"]
        SEC["Security (DPAPI, Approvals, Sandbox, Audit)"]
        VOICE["Voice (STT, TTS, VAD, WakeWord)"]
        VISION["Vision (Opt-in Camera, Face, Gestures)"]
        TOOLS["Tools (Filesystem, System, Clipboard, Telemetry)"]
    end

    subgraph DATA["Local Persistence & Runtimes"]
        PG["PostgreSQL 16 + pgvector"]
        OLLAMA["Ollama (Local LLM & Embeddings)"]
        DPAPI["Windows DPAPI (Credentials & Biometrics)"]
        GROQ["Groq Cloud (Consent-Gated, 2-Message Payload)"]
    end

    STATE <--> BUS
    STATE --> REG
    LIFE --> REG
    REG --> AI & MEM & CONV & AGT & SEC & VOICE & VISION & TOOLS
    AI --> OLLAMA
    AI -.->|"Per-request SHA-256 consent"| GROQ
    MEM & CONV & AGT & SEC --> PG
    SEC --> DPAPI
```

---

## Core Architectural Principles

1. **Strict UI/Core Separation:** `src/jarvis/core/` and all domain services never import `PySide6`. The desktop shell communicates via typed Python interfaces and `EventBus`.
2. **Single Shared Engine:** Closing or hiding HUD, Workspace, or Floating windows does not stop `CoreEngine`. Only tray **Quit** or `Ctrl+Alt+Q` triggers `ShutdownRequested`.
3. **Local-First AI & Fail-Closed Security:** Ollama is primary; Groq requires per-request consent and strict 2-message isolation. Tool execution and camera access fail closed by default.
