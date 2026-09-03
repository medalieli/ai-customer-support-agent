"""policy grounded refund decisions

Revision ID: 20260903_0003
Revises: 20260903_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0003"
down_revision: str | None = "20260903_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refund_decisions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("order_ref", sa.String(160), nullable=False),
        sa.Column("order_number", sa.String(40), nullable=False),
        sa.Column("order_version", sa.String(80), nullable=False),
        sa.Column("policy_document_id", sa.UUID()),
        sa.Column("policy_version_id", sa.UUID()),
        sa.Column("policy_version", sa.String(80)),
        sa.Column("policy_fingerprint", sa.String(64)),
        sa.Column("ruleset_version", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("facts_hash", sa.String(64), nullable=False),
        sa.Column("citation_receipt_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_document_id"],
            ["knowledge_documents.organization_id", "knowledge_documents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_version_id"],
            ["document_versions.organization_id", "document_versions.id"],
        ),
        sa.UniqueConstraint("organization_id", "id"),
    )
    op.create_index("ix_refund_decisions_organization_id", "refund_decisions", ["organization_id"])
    op.create_index("ix_refund_decisions_conversation_id", "refund_decisions", ["conversation_id"])
    op.execute("ALTER TABLE refund_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refund_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON refund_decisions "
        "USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) "
        "WITH CHECK (organization_id = NULLIF("
        "current_setting('app.organization_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.drop_index("ix_refund_decisions_conversation_id", table_name="refund_decisions")
    op.drop_index("ix_refund_decisions_organization_id", table_name="refund_decisions")
    op.drop_table("refund_decisions")
