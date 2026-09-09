"""Recoverable deletion of customer conversations and staff tickets."""

import sqlalchemy as sa
from alembic import op

revision = "20260907_0001"
down_revision = "20260904_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("customer_deleted_at", sa.DateTime(timezone=True)))
    op.add_column("support_tickets", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("support_tickets", "deleted_at")
    op.drop_column("conversations", "customer_deleted_at")
