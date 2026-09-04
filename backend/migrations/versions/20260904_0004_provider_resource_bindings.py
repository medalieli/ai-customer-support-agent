"""trusted provider resource bindings

Revision ID: 20260904_0004
Revises: 20260904_0003
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0004"
down_revision: str | None = "20260904_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON {table} USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)"
        )
    )


def upgrade() -> None:
    op.create_unique_constraint("uq_webhook_org_id", "webhook_events", ["organization_id", "id"])
    op.create_table(
        "provider_resource_bindings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("external_ref", sa.String(160), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("provider_customer_ref", sa.String(160)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "connection_id"],
            ["provider_connections.organization_id", "provider_connections.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "customer_id"], ["customers.organization_id", "customers.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        sa.UniqueConstraint("connection_id", "resource_type", "external_ref", "conversation_id"),
        sa.UniqueConstraint("organization_id", "id"),
    )
    op.create_index(
        "ix_binding_lookup",
        "provider_resource_bindings",
        ["organization_id", "connection_id", "resource_type", "external_ref"],
    )
    op.create_table(
        "webhook_conversation_effects",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("webhook_event_id", sa.UUID(), nullable=False),
        sa.Column("binding_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("logical_key", sa.String(64), nullable=False),
        sa.Column("message_id", sa.UUID(), sa.ForeignKey("messages.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "webhook_event_id"],
            ["webhook_events.organization_id", "webhook_events.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "binding_id"],
            ["provider_resource_bindings.organization_id", "provider_resource_bindings.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
        ),
        sa.UniqueConstraint("webhook_event_id", "binding_id"),
        sa.UniqueConstraint("binding_id", "logical_key"),
    )
    _rls("provider_resource_bindings")
    _rls("webhook_conversation_effects")


def downgrade() -> None:
    op.drop_table("webhook_conversation_effects")
    op.drop_index("ix_binding_lookup", table_name="provider_resource_bindings")
    op.drop_table("provider_resource_bindings")
    op.drop_constraint("uq_webhook_org_id", "webhook_events", type_="unique")
