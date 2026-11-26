# Memory & RAG

`MemoryManager` (`src/jarvis/memory/manager.py`) coordinates **six memory tiers** with **zero automatic permanence** — candidate memories are proposed (`MemoryProposal`) and require explicit user approval before long-term persistence.

---

## Six Memory Tiers

| Tier | Module | Storage | Purpose |
| :--- | :--- | :--- | :--- |
| **1. Working Memory** | `working_memory.py` | In-memory | Active turn scratchpad |
| **2. Short-Term Memory** | `short_term.py` | In-memory | Recent session context |
| **3. Long-Term Semantic** | `long_term.py` | PostgreSQL `memories` (`vector(1024)` + HNSW) | Semantic facts & deduplicated memories (`memory_dedup_threshold`) |
| **4. Episodic Memory** | `long_term.py` (`EpisodeRecord`) | PostgreSQL `episodes` | Conversation summaries |
| **5. User Preferences** | `preferences.py` | PostgreSQL `user_preferences` | Structured user preference records |
| **6. Local Document RAG** | `rag.py` (`LocalDocumentIndexer`) | PostgreSQL `memories` (`kind="rag_chunk"`) | Opt-in local file indexing |

---

## Local Document RAG (`src/jarvis/memory/rag.py`)

- **Supported file types:** `.md`, `.txt`, `.py`, `.rst`, `.json`, `.toml`.
- **Deterministic chunking:** `chunk_text()` splits files into overlapping chunks (`chunk_size=800`, `overlap=120` defaults) and replaces prior chunks per `source_key` (`rag:<sha256[:24]>`).
- **Embeddings:** Generated locally via `OllamaEmbeddingService` (`nomic-embed-text`, `1024` dimensions).
- **Privacy Guarantee:** Retrieved RAG chunks and long-term memories are **strictly excluded** from cloud payloads (`src/jarvis/intelligence/cloud_payload.py`).
