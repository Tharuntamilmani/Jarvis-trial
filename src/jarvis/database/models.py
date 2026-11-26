"""ORM models for JARVIS.

Embedding columns use the pgvector ``VECTOR`` type whose dimension is fixed
per database by migrations (JARVIS_EMBEDDING_DIM at migration time). The
``dimension`` passed to :class:`jarvis.database.types.VECTOR` here is
informational only — the authoritative definition lives in the Alembic
migration, which is the only sanctioned way to change it.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from jarvis.database.types import VECTOR
from jarvis.config import get_settings

_EMBEDDING_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    """Declarative base for all JARVIS models."""


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# --- Identity ------------------------------------------------------------------


class User(Base):
    """User profile. Biometric data is intentionally NOT stored here —
    face embeddings live only in DPAPI-encrypted local storage."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(120), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Conversation memory -------------------------------------------------------


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), default="Untitled")
    mode: Mapped[str] = mapped_column(String(20), default="workspace")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Stage 2.8: reorders the conversation list. Updated on a new turn and on
    # rename — never on streaming checkpoints. Raw-SQL writers must set this
    # explicitly: ``onupdate`` only applies to ORM updates.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # --- Stage 2.8 persistence ---------------------------------------------
    # Deterministic ordering. Always ``ORDER BY seq``; never by timestamp.
    seq: Mapped[int] = mapped_column(BigInteger)
    # Logical turn identity shared by the original user+assistant pair. A
    # retry is assistant-only and carries its own request ID.
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # pending | streaming | completed | failed | cancelled | interrupted
    status: Mapped[str] = mapped_column(String(20), default="completed")
    # Safe machine-readable category only; human detail stays in the UI layer.
    error: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    privacy: Mapped[str | None] = mapped_column(String(20))  # local | cloud
    owner_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Optimistic-concurrency guard for checkpoints and terminal writes.
    content_version: Mapped[int] = mapped_column(Integer, default=0)
    retry_of_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Long-term memory (semantic + episodic) -------------------------------------


class Memory(Base):
    """Tier-4 semantic memory (facts, knowledge). Promoted only by explicit
    user action or approved consolidation — never automatically."""

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(30), default="fact")
    content: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(VECTOR(_EMBEDDING_DIM))
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(30), default="user")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Episode(Base):
    """Tier-5 episodic memory (dated events)."""

    __tablename__ = "episodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    content: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(VECTOR(_EMBEDDING_DIM))
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserPreference(Base):
    """Tier-6 user preferences (explicit settings + learned habits)."""

    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    explicit: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = ({"schema": None},)


# --- Agents ----------------------------------------------------------------------


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    agent: Mapped[str] = mapped_column(String(60))
    input: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    error_category: Mapped[str | None] = mapped_column(String(40))
    owner_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Security ----------------------------------------------------------------------


class AuditLog(Base):
    """Append-only execution audit (Phase 7 adds the dual-write)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(120))
    tool: Mapped[str | None] = mapped_column(String(120))
    args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(60))
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Settings -----------------------------------------------------------------------


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "AgentRun",
    "AppSetting",
    "AuditLog",
    "Base",
    "Conversation",
    "Episode",
    "Memory",
    "Message",
    "User",
    "UserPreference",
]

