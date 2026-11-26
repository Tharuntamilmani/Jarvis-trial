"""baseline schema: phase 1

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-17

Embedding dimensions are read from JARVIS_EMBEDDING_DIM (Settings) at
migration time and are FIXED PER DATABASE afterwards. Changing the
dimension requires a new explicit migration plus a re-embed pass — there is
no runtime dimension switching (docs/ARCHITECTURE.md §7).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.types import UserDefinedType

from jarvis.config import get_settings

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_DIM = get_settings().embedding_dim


class VECTOR(UserDefinedType):
    """pgvector VECTOR(n) — dimension fixed per database."""

    cache_ok = True

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def get_col_spec(self, **kw: object) -> str:  # type: ignore[override]
        return f"VECTOR({self.dimension})"


def upgrade() -> None:
    # Extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')

    _UUID_PK = dict(
        type_=pg.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )

    # users
    op.create_table(
        "users",
        sa.Column("id", **_UUID_PK),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("preferences", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # conversations
    op.create_table(
        "conversations",
        sa.Column("id", **_UUID_PK),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False, server_default="Untitled"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="workspace"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    # messages
    op.create_table(
        "messages",
        sa.Column("id", **_UUID_PK),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tokens", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    # memories (semantic, tier 4) — dimension fixed at migration time
    op.create_table(
        "memories",
        sa.Column("id", **_UUID_PK),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False, server_default="fact"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", VECTOR(_DIM), nullable=True),
        sa.Column("importance", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("source", sa.String(30), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "CREATE INDEX ix_memories_embedding_hnsw ON memories"
        " USING hnsw (embedding vector_cosine_ops)"
    )

    # episodes (episodic, tier 5)
    op.create_table(
        "episodes",
        sa.Column("id", **_UUID_PK),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", VECTOR(_DIM), nullable=True),
        sa.Column("importance", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "CREATE INDEX ix_episodes_embedding_hnsw ON episodes"
        " USING hnsw (embedding vector_cosine_ops)"
    )

    # user_preferences (tier 6)
    op.create_table(
        "user_preferences",
        sa.Column("id", **_UUID_PK),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("explicit", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])

    # agent_runs
    op.create_table(
        "agent_runs",
        sa.Column("id", **_UUID_PK),
        sa.Column("agent", sa.String(60), nullable=False),
        sa.Column("input", sa.Text, nullable=False),
        sa.Column("output", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # audit_log (append-only by application convention)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("tool", sa.String(120), nullable=True),
        sa.Column("args", sa.JSON, nullable=True),
        sa.Column("approved_by", sa.String(60), nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Block updates/deletes at the database level: audit is append-only.
    op.execute(
        "CREATE RULE audit_log_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING"
    )
    op.execute(
        "CREATE RULE audit_log_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING"
    )

    # app_settings
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("audit_log")
    op.drop_table("agent_runs")
    op.drop_table("user_preferences")
    op.drop_table("episodes")
    op.drop_table("memories")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
    op.execute("DROP EXTENSION IF EXISTS vector")
