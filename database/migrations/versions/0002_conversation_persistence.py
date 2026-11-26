"""conversation persistence: deterministic turns, checkpoints, recovery

Revision ID: 0002_conversation_persistence
Revises: 0001_baseline
Create Date: 2026-11-26

Stage 2.8 (Gate A). This migration is additive and non-destructive: it never
drops, truncates or recreates existing conversation data. Every new column is
added nullable (or with a safe server default), backfilled deterministically,
validated, and only then made NOT NULL / constrained.

Message ordering is authoritative through ``seq``. Timestamps are never used
for ordering, only for the one-time deterministic backfill of legacy rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002_conversation_persistence"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

# The role contract already implied by the 0001 schema comment and the
# application. Verified against existing data before the constraint is added.
_ALLOWED_ROLES = ("user", "assistant", "system", "tool")
_ALLOWED_STATUSES = (
    "pending",
    "streaming",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)
_ALLOWED_PRIVACY = ("local", "cloud")
_ALLOWED_ERRORS = (
    "persistence_error",
    "provider_error",
    "cancelled",
    "timeout",
    "interrupted",
    "unknown",
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    bind = op.get_bind()

    # --- conversations -----------------------------------------------------
    # Added nullable, backfilled from created_at, then constrained. Adding it
    # with a server default would stamp every legacy row with "now" instead of
    # its real creation time.
    op.add_column(
        "conversations",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE conversations SET updated_at = created_at")
    op.alter_column(
        "conversations",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])

    # --- messages: new nullable columns ------------------------------------
    op.add_column("messages", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.add_column(
        "messages",
        sa.Column("request_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("messages", sa.Column("status", sa.String(20), nullable=True))
    op.add_column("messages", sa.Column("error", sa.String(40), nullable=True))
    op.add_column("messages", sa.Column("provider", sa.String(40), nullable=True))
    op.add_column("messages", sa.Column("model", sa.String(120), nullable=True))
    op.add_column("messages", sa.Column("privacy", sa.String(20), nullable=True))
    op.add_column(
        "messages",
        sa.Column("owner_session_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("content_version", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "messages",
        sa.Column("retry_of_request_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- backfill ----------------------------------------------------------
    # Deterministic per-conversation sequence numbers. Ties on created_at are
    # broken by id so the result is reproducible.
    op.execute(
        """
        UPDATE messages AS m
        SET seq = ordered.rn
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY conversation_id
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM messages
        ) AS ordered
        WHERE m.id = ordered.id
          AND m.seq IS NULL
        """
    )
    # Legacy messages are completed input; no historical provider, model or
    # privacy information is invented.
    op.execute("UPDATE messages SET status = 'completed' WHERE status IS NULL")
    op.execute("UPDATE messages SET content_version = 0 WHERE content_version IS NULL")
    op.execute("UPDATE messages SET updated_at = created_at WHERE updated_at IS NULL")

    # --- validate ----------------------------------------------------------
    unexpected_roles = {
        row[0]
        for row in bind.execute(
            sa.text("SELECT DISTINCT role FROM messages")
        ).fetchall()
    } - set(_ALLOWED_ROLES)
    if unexpected_roles:
        raise RuntimeError(
            "unexpected message role value(s) present; refusing to narrow the "
            f"role contract: {sorted(unexpected_roles)}"
        )

    null_seq = bind.execute(
        sa.text("SELECT count(*) FROM messages WHERE seq IS NULL")
    ).scalar_one()
    if null_seq:
        raise RuntimeError(f"{null_seq} message row(s) still lack a sequence number")

    duplicate_seq = bind.execute(
        sa.text(
            "SELECT count(*) FROM ("
            "  SELECT conversation_id, seq FROM messages"
            "  GROUP BY conversation_id, seq HAVING count(*) > 1"
            ") AS dup"
        )
    ).scalar_one()
    if duplicate_seq:
        raise RuntimeError(
            f"{duplicate_seq} duplicate (conversation_id, seq) pair(s) found"
        )

    # --- make required columns non-null ------------------------------------
    op.alter_column("messages", "seq", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column(
        "messages",
        "status",
        existing_type=sa.String(20),
        nullable=False,
        server_default="completed",
    )
    op.alter_column(
        "messages",
        "content_version",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="0",
    )
    op.alter_column(
        "messages",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

    # --- constraints -------------------------------------------------------
    op.create_unique_constraint(
        "uq_messages_conversation_seq",
        "messages",
        ["conversation_id", "seq"],
    )
    op.create_check_constraint("ck_messages_seq_non_negative", "messages", "seq >= 0")
    op.create_check_constraint(
        "ck_messages_role",
        "messages",
        f"role IN ({_sql_list(_ALLOWED_ROLES)})",
    )
    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        f"status IN ({_sql_list(_ALLOWED_STATUSES)})",
    )
    op.create_check_constraint(
        "ck_messages_privacy",
        "messages",
        f"privacy IS NULL OR privacy IN ({_sql_list(_ALLOWED_PRIVACY)})",
    )
    op.create_check_constraint(
        "ck_messages_error",
        "messages",
        f"error IS NULL OR error IN ({_sql_list(_ALLOWED_ERRORS)})",
    )
    # User input is persisted as completed input; only assistant rows move
    # through generation states.
    op.create_check_constraint(
        "ck_messages_user_completed",
        "messages",
        "role <> 'user' OR status = 'completed'",
    )

    # One logical turn has one user row and one assistant row. Legacy rows have
    # a NULL request_id and are excluded by the partial predicate; an
    # assistant-only retry carries a *new* request_id, so it is unaffected.
    op.create_index(
        "uq_messages_request_role",
        "messages",
        ["request_id", "role"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    # Recovery scan (Gate B): unfinished assistant rows by owning process.
    op.create_index(
        "ix_messages_owner_status",
        "messages",
        ["owner_session_id", "status"],
        postgresql_where=sa.text("status IN ('pending', 'streaming')"),
    )


def downgrade() -> None:
    op.drop_index("ix_messages_owner_status", table_name="messages")
    op.drop_index("uq_messages_request_role", table_name="messages")

    for name in (
        "ck_messages_user_completed",
        "ck_messages_error",
        "ck_messages_privacy",
        "ck_messages_status",
        "ck_messages_role",
        "ck_messages_seq_non_negative",
    ):
        op.drop_constraint(name, "messages", type_="check")
    op.drop_constraint("uq_messages_conversation_seq", "messages", type_="unique")

    for column in (
        "updated_at",
        "retry_of_request_id",
        "content_version",
        "owner_session_id",
        "privacy",
        "model",
        "provider",
        "error",
        "status",
        "request_id",
        "seq",
    ):
        op.drop_column("messages", column)

    op.drop_index("ix_conversations_updated_at", table_name="conversations")
    op.drop_column("conversations", "updated_at")
