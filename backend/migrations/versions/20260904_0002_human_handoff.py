"""durable human handoff

Revision ID: 20260904_0002
Revises: 20260904_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0002"
down_revision: str | None = "20260904_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("ownership_state", sa.String(30), nullable=False, server_default="ai_active"),
    )
    op.add_column("support_tickets", sa.Column("provider_ref", sa.String(160)))
    op.add_column(
        "support_tickets", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("support_tickets", sa.Column("resolved_at", sa.DateTime(timezone=True)))
    op.create_index(
        "uq_support_ticket_active_conversation",
        "support_tickets",
        ["organization_id", "conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'IN_PROGRESS')"),
    )


def downgrade() -> None:
    op.drop_index("uq_support_ticket_active_conversation", table_name="support_tickets")
    op.drop_column("support_tickets", "resolved_at")
    op.drop_column("support_tickets", "version")
    op.drop_column("support_tickets", "provider_ref")
    op.drop_column("conversations", "ownership_state")
