"""add durable agent orchestration

Revision ID: 20260903_0001
Revises: 7d1f3c9a2b10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0001"
down_revision: str | None = "7d1f3c9a2b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_policy(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON {table} USING (organization_id = "
            "NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK "
            "(organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)"
        )
    )


def upgrade() -> None:
    op.create_table(
        "agent_threads",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("checkpoint_thread_id", sa.String(160), nullable=False, unique=True),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(40), nullable=False, server_default="idle"),
        sa.Column("interrupt_reason", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        sa.UniqueConstraint("organization_id", "conversation_id"),
        sa.UniqueConstraint("organization_id", "id"),
    )
    op.create_index("ix_agent_threads_organization_id", "agent_threads", ["organization_id"])
    op.create_index("ix_agent_threads_conversation_id", "agent_threads", ["conversation_id"])
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="running"),
        sa.Column("state_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"], ["agent_threads.organization_id", "agent_threads.id"]
        ),
        sa.UniqueConstraint("organization_id", "thread_id", "idempotency_key"),
        sa.UniqueConstraint("organization_id", "id"),
    )
    op.create_index("ix_agent_runs_organization_id", "agent_runs", ["organization_id"])
    op.create_index("ix_agent_runs_thread_id", "agent_runs", ["thread_id"])
    op.create_index(
        "uq_agent_runs_one_running",
        "agent_runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "agent_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"], ["agent_runs.organization_id", "agent_runs.id"]
        ),
        sa.UniqueConstraint("run_id", "sequence_number"),
    )
    op.create_index("ix_agent_events_organization_id", "agent_events", ["organization_id"])
    op.create_index("ix_agent_events_run_id", "agent_events", ["run_id"])
    for table in ("agent_threads", "agent_runs", "agent_events"):
        _tenant_policy(table)


def downgrade() -> None:
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
    op.drop_table("agent_threads")
