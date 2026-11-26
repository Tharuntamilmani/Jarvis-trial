"""agent runtime: idempotency, session ownership, status transitions

Revision ID: 0003_agent_runtime
Revises: 0002_conversation_persistence
Create Date: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003_agent_runtime"
down_revision = "0002_conversation_persistence"
branch_labels = None
depends_on = None

_ALLOWED_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "interrupted",
)


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("idempotency_key", sa.String(120), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("error_category", sa.String(40), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("owner_session_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("parent_run_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE agent_runs SET updated_at = created_at")
    op.alter_column(
        "agent_runs",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

    op.create_index(
        "uq_agent_runs_idempotency",
        "agent_runs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_runs_status",
        "agent_runs",
        ["status"],
    )
    allowed = ", ".join(f"'{s}'" for s in _ALLOWED_STATUSES)
    op.create_check_constraint(
        "ck_agent_runs_status",
        "agent_runs",
        f"status IN ({allowed})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_runs_status", "agent_runs", type_="check")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("uq_agent_runs_idempotency", table_name="agent_runs")
    op.drop_column("agent_runs", "updated_at")
    op.drop_column("agent_runs", "parent_run_id")
    op.drop_column("agent_runs", "owner_session_id")
    op.drop_column("agent_runs", "error_category")
    op.drop_column("agent_runs", "idempotency_key")
