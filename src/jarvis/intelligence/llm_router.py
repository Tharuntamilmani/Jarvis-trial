"""Backward-compatible alias for :mod:`jarvis.intelligence.router`.

The Phase 1 scaffold reserved the name ``llm_router``; Stage 2.4 implements
the unified router in ``router.py``. This alias keeps older imports working
without duplicating any logic.
"""

from __future__ import annotations

from jarvis.intelligence.router import (
    CloudConsentDeniedError,
    LLMRouter,
    NoAvailableProviderError,
    RouteOutcome,
    RoutingError,
)

__all__ = [
    "CloudConsentDeniedError",
    "LLMRouter",
    "NoAvailableProviderError",
    "RouteOutcome",
    "RoutingError",
]
