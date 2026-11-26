# Cloud Data Policy

JARVIS is **local-first** (Ollama). Cloud fallback via Groq is optional, disabled unless configured, and governed by two strict modules: `src/jarvis/intelligence/cloud_payload.py` and `src/jarvis/intelligence/consent.py`.

---

## Strict 2-Message Isolation (`CloudPayload`)

`build_cloud_payload()` is the **only** sanctioned path for creating an outbound cloud request. A `CloudPayload` contains **exactly two messages**:

1. The static `JARVIS_SYSTEM_PROMPT` (`src/jarvis/intelligence/prompts.py`).
2. The **final** `user` message of the current turn.

**Strictly excluded by construction:**
- Prior conversation turns / history
- Long-term memories and episodic summaries
- Local RAG document chunks
- Files, attachments, and tool outputs
- Machine info, environment variables, debug info, and internal state

---

## Per-Request SHA-256 Consent (`ConsentManager`)

- **Fail-Closed Default:** Default decision is **DENIED**. Timeout (`cloud_consent_timeout_seconds = 30.0`), missing responder, or errors all deny.
- **No Persistent Blanket Consent:** Consent is never inferred from settings or prior turns.
- **Fingerprint Binding:** A SHA-256 `fingerprint` binds the exact `CloudPayload` to `(request_id, fingerprint)`. `validate_cloud_dispatch()` re-verifies the payload and serialized body immediately before sending to Groq.
