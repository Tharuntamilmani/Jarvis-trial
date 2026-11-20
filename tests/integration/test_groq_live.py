"""Opt-in live Groq integration tests (Stage 2.3).

⚠ CLOUD API — these tests consume real Groq account quota and are NOT part
of the default suite. They run only when BOTH conditions hold:

1. A Groq key is stored in the DPAPI credential store (``groq_api_key``), AND
2. the environment variable ``JARVIS_RUN_GROQ_LIVE=1`` is set explicitly.

Requests are minimal: ``max_tokens`` capped at 10. Credentials are never
printed. Usage: set JARVIS_RUN_GROQ_LIVE=1 and run this file explicitly.
"""

from __future__ import annotations

import os

import pytest

from jarvis.config import get_settings
from jarvis.security.credentials import CredentialStore


def _has_stored_key() -> bool:
    try:
        store = CredentialStore(get_settings())
        return store.load_secret("groq_api_key") is not None
    except Exception:  # noqa: BLE001
        return False


# Opt-in double gate: a stored key AND an explicit environment flag.
# Never part of the default test run (cloud API — consumes quota).
_OPT_IN = os.environ.get("JARVIS_RUN_GROQ_LIVE", "") == "1"
pytestmark = pytest.mark.skipif(
    not (_HAS_STORED_KEY := _has_stored_key()) or not _OPT_IN,
    reason=(
        "opt-in cloud test: requires a DPAPI-stored groq_api_key AND "
        "JARVIS_RUN_GROQ_LIVE=1 (consumes Groq quota)"
    ),
)


def _provider():
    from jarvis.intelligence.providers.groq_provider import GroqProvider

    return GroqProvider(get_settings())


def _request():
    from jarvis.intelligence.providers.base import ChatMessage, ChatRequest

    return ChatRequest(
        model=None,  # configured default model
        messages=[ChatMessage("user", "Say: OK")],
        request_id="groq-live-1",
        max_tokens=10,  # quota cap per project rules
    )


@pytest.mark.asyncio
async def test_live_chat_response() -> None:
    response = await _provider().chat(_request())
    assert response.provider == "groq"
    assert response.content.strip() != ""


@pytest.mark.asyncio
async def test_live_streamed_response() -> None:
    from jarvis.intelligence.providers.base import (
        StreamDone,
        StreamStart,
        Token,
    )

    events = []
    async for event in _provider().stream(_request()):
        events.append(event)
    assert isinstance(events[0], StreamStart)
    assert isinstance(events[-1], StreamDone)
    assert len([e for e in events if isinstance(e, Token)]) >= 1
