# JARVIS AI — MASTER DEVELOPMENT INSTRUCTIONS

## 1. PROJECT VISION

Build JARVIS, a modular, multimodal, personal AI assistant
for Windows 11.

Jarvis must be a native desktop application.

It must not be a web-based application.

## 2. STRICT ARCHITECTURE RULES

- Native Windows desktop application only.
- Use Python 3.12.
- Use PySide6 for the complete UI.
- Use Qt Widgets and custom painting.
- Use QSS for styling.
- Use QPropertyAnimation for animations.
- Use asyncio and qasync for asynchronous operations.
- Use internal Python interfaces for module communication.
- Use an asynchronous event bus.
- Keep business logic independent of the UI.

STRICTLY PROHIBITED:
- Next.js
- React
- HTML frontend
- Browser-based dashboards
- FastAPI
- Flask
- Electron
- WebView-based application interfaces

Do not introduce a web server as the internal
communication layer.

## 3. DESKTOP INTERFACE

Implement three interface modes:

A. FULL-SCREEN HUD
- Futuristic JARVIS interface.
- Animated central AI visualization.
- Voice activity visualization.
- System monitoring.
- Notifications.
- Agent activity.

B. PREMIUM WORKSPACE
- Chat interface.
- Conversation history.
- Memory management.
- Agent management.
- Settings.
- Model selection.
- Activity history.

C. FLOATING ASSISTANT
- Always-on-top compact window.
- Animated assistant orb.
- Voice activation.
- Quick text commands.
- Expand and minimize controls.

All three modes must use the same application
state and core services.

## 4. TECHNOLOGY STACK

- Python 3.12
- PySide6
- qasync
- asyncio
- SQLAlchemy
- PostgreSQL
- pgvector
- Ollama
- Qwen3 8B
- Groq
- faster-whisper
- Piper TTS
- OpenWakeWord
- OpenCV
- InsightFace
- MediaPipe
- PyInstaller

Install dependencies progressively.

## 5. CORE ARCHITECTURE

Separate modules into:

- Core engine
- Intelligence
- Memory
- Agents
- Perception
- Voice
- Vision
- Tools
- Execution
- Integrations
- Database
- Desktop interface

The core engine must not depend on PySide6.

The desktop interface must communicate with
core services through defined interfaces and events.

## 6. AI INTELLIGENCE

- Ollama is the primary local LLM provider.
- Groq is the cloud fallback provider.
- Implement a unified model interface.
- Support model selection.
- Support streaming responses.
- Handle provider timeouts and failures.
- Never silently send private conversation data
  to a cloud provider without appropriate consent.

## 7. MEMORY

Use PostgreSQL and pgvector.

Support:
- Short-term memory
- Long-term memory
- Conversation history
- Semantic retrieval
- User preferences

## 8. SECURITY

- Never hardcode API keys.
- Use environment variables.
- Protect sensitive user information.
- Require approval for sensitive computer actions.
- Never execute arbitrary generated shell commands.
- Keep audit logs for execution actions.
- Validate all tool inputs.

## 9. DEVELOPMENT RULES

- Analyze before implementation.
- Implement one phase at a time.
- Use modular, maintainable code.
- Add Python type hints.
- Write unit and integration tests.
- Avoid unnecessary dependencies.
- Never rewrite working modules unnecessarily.
- Do not delete files without approval.
- Explain architectural changes before making them.
- Report changed files and test results.

## 10. CURRENT DEVELOPMENT PHASE

Phase 1: Native desktop foundation.

Build:
- Application entry point
- Core application lifecycle
- Configuration
- Logging
- Event bus
- Service registry
- PostgreSQL connection
- Native PySide6 application shell
- HUD mode
- Workspace mode
- Floating mode
- Mode switching
- Basic service status indicators
- Automated tests

Do not implement advanced voice,
vision, agents or computer automation yet.