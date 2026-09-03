"""confirmed shipping address actions

Revision ID: 20260903_0002
Revises: 20260903_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0002"
down_revision: str | None = "20260903_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pending_actions", sa.Column("run_id", sa.UUID(), nullable=True))
    op.add_column("pending_actions", sa.Column("session_id", sa.UUID(), nullable=True))
    op.add_column("pending_actions", sa.Column("order_ref", sa.String(160), nullable=True))
    op.add_column("pending_actions", sa.Column("order_number", sa.String(40), nullable=True))
    op.add_column("pending_actions", sa.Column("order_version", sa.String(80), nullable=True))
    op.add_column(
        "pending_actions", sa.Column("encrypted_payload", sa.LargeBinary(), nullable=True)
    )
    op.add_column("pending_actions", sa.Column("action_hash", sa.String(64), nullable=True))
    op.add_column("pending_actions", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column("pending_actions", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.add_column("pending_actions", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("pending_actions", sa.Column("result_version", sa.String(80)))
    op.add_column("pending_actions", sa.Column("failure_code", sa.String(80)))
    # Earlier milestones never created pending-action rows.
    for name in (
        "run_id",
        "session_id",
        "order_ref",
        "order_number",
        "order_version",
        "encrypted_payload",
        "action_hash",
        "idempotency_key",
    ):
        op.alter_column("pending_actions", name, nullable=False)
    op.create_index("ix_pending_actions_run_id", "pending_actions", ["run_id"])
    op.create_unique_constraint(
        "uq_pending_actions_org_id", "pending_actions", ["organization_id", "id"]
    )
    op.create_unique_constraint(
        "uq_pending_actions_org_idempotency",
        "pending_actions",
        ["organization_id", "idempotency_key"],
    )
    op.create_index(
        "uq_pending_actions_one_active_conversation",
        "pending_actions",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("uq_pending_actions_one_active_conversation", table_name="pending_actions")
    op.drop_constraint("uq_pending_actions_org_idempotency", "pending_actions", type_="unique")
    op.drop_constraint("uq_pending_actions_org_id", "pending_actions", type_="unique")
    op.drop_index("ix_pending_actions_run_id", table_name="pending_actions")
    for name in (
        "failure_code",
        "result_version",
        "completed_at",
        "approved_at",
        "idempotency_key",
        "action_hash",
        "encrypted_payload",
        "order_version",
        "order_number",
        "order_ref",
        "session_id",
        "run_id",
    ):
        op.drop_column("pending_actions", name)
