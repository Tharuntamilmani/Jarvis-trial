# Chat & Conversations

JARVIS provides native streaming chat in **Workspace** and quick command interaction in **HUD** and **Floating Assistant**, backed by durable PostgreSQL persistence.

---

## Key Capabilities

- **Streaming Responses:** `ChatService` (`src/jarvis/intelligence/chat_service.py`) streams tokens live into PySide6 message bubbles with a **Stop** control to cancel in-flight generations cleanly.
- **Durable Turn Persistence:** `ConversationService` (`src/jarvis/conversations/service.py`) persists turns via `ConversationRepository` with a bounded persistence deadline (`db_persistence_deadline_seconds`).
- **Ambiguous-Outcome Reconciliation:** Each turn is bound to a `request_id` UUID and process `session_id` so interrupted or timed-out database writes can be reconciled without duplicating messages.
- **Window Independence:** Closing or switching away from Workspace never drops an active conversation or generation.
- **Inline Cloud Consent Card:** When local Ollama is unavailable and Groq is configured, Workspace renders an inline consent card displaying the exact 2-message scope before any cloud request is sent.
