"""consented sales leads

Revision ID: 20260904_0001
Revises: 20260903_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0001"
down_revision: str | None = "20260903_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pending_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "pending_action_id"],
            ["pending_actions.organization_id", "pending_actions.id"],
        ),
        sa.UniqueConstraint("organization_id", "pending_action_id"),
    )
    op.create_index("ix_consent_records_organization_id", "consent_records", ["organization_id"])
    op.execute("ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE consent_records FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON consent_records USING "
        "(organization_id = nullif(current_setting('app.organization_id', true), '')::uuid) "
        "WITH CHECK (organization_id = "
        "nullif(current_setting('app.organization_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_index("ix_consent_records_organization_id", table_name="consent_records")
    op.drop_table("consent_records")
