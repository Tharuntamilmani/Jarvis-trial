"""Approved prompts for JARVIS.

The cloud payload sanitizer (``cloud_payload.py``) validates against
``JARVIS_SYSTEM_PROMPT`` exactly — this constant is the ONLY sanctioned
system prompt content that may ever be transmitted to a cloud provider.
Changing it requires a deliberate code change (deliberately not a setting,
so consent previews always reflect reality).
"""

from __future__ import annotations

JARVIS_SYSTEM_PROMPT = (
    "You are JARVIS, a local desktop AI assistant. "
    "Answer clearly and concisely. "
    "You receive only the user's current message — no conversation history."
)

SYSTEM_PROMPT_VERSION = "v1"
