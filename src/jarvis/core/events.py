"""Typed event contract for the JARVIS event bus.

Every event crossing module boundaries is a frozen dataclass subclassing
:class:`Event`. The class name (converted to snake case) is used as the
topic name, so subscribers can filter by type while the transport stays a
simple string-based pub/sub.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    """Base event. Subclasses define the topic and payload fields."""

    timestamp: float = field(default_factory=time.monotonic, init=False)

    @property
    def name(self) -> str:
        """Topic name, e.g. ``service_status_changed``."""
        return _to_snake_case(type(self).__name__)

    def as_dict(self) -> dict[str, Any]:
        """Serialise the event payload (excluding the timestamp)."""
        return {
            k: v
            for k, v in dataclasses.as_dict(self).items()
            if k != "timestamp"
        }


def _to_snake_case(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


# --- Lifecycle -------------------------------------------------------------


@dataclass(frozen=True)
class StartupCompleted(Event):
    """Core engine finished starting all services."""


@dataclass(frozen=True)
class ShutdownRequested(Event):
    """Graceful application shutdown was requested (tray → Quit)."""


@dataclass(frozen=True)
class ShutdownCompleted(Event):
    """Core engine finished shutting down."""


# --- Services --------------------------------------------------------------


@dataclass(frozen=True)
class ServiceStatusChanged(Event):
    """A registered service changed health/status."""

    service: str = ""
    status: str = "unknown"  # healthy | degraded | unavailable | starting | stopped
    detail: str = ""


# --- Security --------------------------------------------------------------


@dataclass(frozen=True)
class KillSwitchTriggered(Event):
    """Emergency stop engaged — all cancellable work must halt."""

    reason: str = ""


@dataclass(frozen=True)
class KillSwitchRearmed(Event):
    """Emergency stop disengaged; normal operation may resume."""


@dataclass(frozen=True)
class ApprovalRequested(Event):
    """A sensitive action awaits explicit user approval."""

    request_id: str = ""
    action: str = ""
    summary: str = ""


@dataclass(frozen=True)
class ApprovalDecided(Event):
    """An approval request was decided."""

    request_id: str = ""
    approved: bool = False
    decided_by: str = "default"  # user | timeout | fail_closed_default


@dataclass(frozen=True)
class AuditRecordWritten(Event):
    """An execution-audit record was appended."""

    action: str = ""
    result: str = ""


# --- Intelligence providers -------------------------------------------------


@dataclass(frozen=True)
class ProviderHealthChanged(Event):
    """An AI provider's availability changed."""

    provider: str = ""
    available: bool = False
    detail: str = ""


@dataclass(frozen=True)
class ProviderSelected(Event):
    """The router chose a provider/model for a request."""

    request_id: str = ""
    provider: str = ""
    model: str = ""
    privacy: str = ""  # local | cloud


# --- Chat streaming (Phase 2) ------------------------------------------------


@dataclass(frozen=True)
class ChatStreamStarted(Event):
    """A chat stream was accepted and started.

    ``privacy`` is additive metadata (``local`` | ``cloud``) forwarded from
    ``StreamRouted`` so every desktop mode can display where inference ran
    without inspecting provider internals.
    """

    request_id: str = ""
    provider: str = ""
    model: str = ""
    privacy: str = ""


@dataclass(frozen=True)
class ChatToken(Event):
    """One streamed token. ``seq`` is monotonic per request (deduped)."""

    request_id: str = ""
    seq: int = 0
    text: str = ""


@dataclass(frozen=True)
class ChatStreamCompleted(Event):
    """A chat stream finished successfully."""

    request_id: str = ""
    content: str = ""
    tokens_used: int = 0


@dataclass(frozen=True)
class ChatStreamFailed(Event):
    """A chat stream failed (partial content is preserved upstream)."""

    request_id: str = ""
    detail: str = ""  # human-readable; never contains credentials
    partial_content: str = ""  # exactly the tokens published before failure


@dataclass(frozen=True)
class ChatStreamCancelled(Event):
    """A chat stream was cancelled (Stop / kill switch); partial kept."""

    request_id: str = ""
    partial_content: str = ""


@dataclass(frozen=True)
class AssistantStateChanged(Event):
    """The aggregate assistant state changed (idle | thinking | speaking)."""

    state: str = "idle"


@dataclass(frozen=True)
class CloudFallbackProposed(Event):
    """Local inference failed; the UI should ask for cloud consent.

    Carries ONLY safe metadata — never credentials or hidden context.
    """

    request_id: str = ""
    reason: str = ""  # human-readable, credential-free
    provider: str = ""  # target cloud provider id
    model: str = ""  # target model ("" = provider default)
    payload_preview: str = ""  # exact outbound scope, human-safe
    payload_fingerprint: str = ""  # sha256 binding consent ↔ payload
    scope: str = ""  # scope description


@dataclass(frozen=True)
class CloudConsentDecided(Event):
    """A cloud consent decision was finalized (granted or denied)."""

    request_id: str = ""
    granted: bool = False
    decided_by: str = "fail_closed"  # user | timeout | fail_closed
    fingerprint: str = ""
