# Privacy Guarantees

JARVIS enforces strict boundaries between local user data, logs, database storage, and optional cloud AI.

---

## Data Boundary Matrix

| Data Category | Stored Locally? | Sent to Cloud AI? | Logged in Plaintext? |
| :--- | :--- | :--- | :--- |
| **Conversation History** | PostgreSQL (`conversations`, `messages`) | **Never** | No |
| **Memories & RAG Chunks** | PostgreSQL (`memories`, `episodes`) | **Never** | No |
| **API Keys (`groq_api_key`)** | Win32 DPAPI (`CredentialStore`) | Used only in Auth header when consented | **Never** (redacted) |
| **Face Embeddings** | Win32 DPAPI (`.bio.bin`, opt-in) | **Never** | **Never** |
| **Raw Camera Frames / Crops** | Ephemeral RAM only while camera active | **Never** | **Never** |
| **Current User Message** | PostgreSQL (`messages`) | **Only** on explicit per-request consent | Redacted if sensitive |

---

## Hardware & Memory Privacy

- **Camera Off by Default:** Camera never starts automatically on launch. Kill switch immediately halts capture and clears in-memory frames.
- **Zero Auto-Permanence in Memory:** `MemoryManager` requires explicit user approval of `MemoryProposal`s before persisting long-term memories.
- **Log Redaction:** `redact_audit_detail()` strips secret patterns before writing to `audit_log`, `logs/audit.jsonl`, or exported diagnostics.
