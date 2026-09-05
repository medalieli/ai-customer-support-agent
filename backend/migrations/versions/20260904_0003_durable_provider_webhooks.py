"""durable provider webhook inbox

Revision ID: 20260904_0003
Revises: 20260904_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0003"
down_revision: str | None = "20260904_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organization_id = "
            "NULLIF(current_setting('app.organization_id', true), '')::uuid) "
            "WITH CHECK (organization_id = "
            "NULLIF(current_setting('app.organization_id', true), '')::uuid)"
        )
    )


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("endpoint_key", sa.String(120), nullable=False),
        sa.Column("encrypted_current_secret", sa.LargeBinary(), nullable=False),
        sa.Column("encrypted_previous_secret", sa.LargeBinary()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "id"),
        sa.UniqueConstraint("provider", "endpoint_key"),
    )
    op.drop_constraint(
        "webhook_events_organization_id_provider_external_event_id_key",
        "webhook_events",
        type_="unique",
    )
    op.drop_column("webhook_events", "signature_valid")
    op.alter_column("webhook_events", "status", type_=sa.String(20), existing_type=sa.String(10))
    for col in [
        sa.Column("connection_id", sa.UUID()),
        sa.Column("topic", sa.String(120)),
        sa.Column("encrypted_payload", sa.LargeBinary()),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("safe_error", sa.String(120)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("webhook_events", col)
    op.execute("DELETE FROM webhook_events")
    op.alter_column("webhook_events", "connection_id", nullable=False)
    op.alter_column("webhook_events", "topic", nullable=False)
    op.alter_column("webhook_events", "encrypted_payload", nullable=False)
    op.create_foreign_key(
        "fk_webhook_connection",
        "webhook_events",
        "provider_connections",
        ["organization_id", "connection_id"],
        ["organization_id", "id"],
    )
    op.create_unique_constraint(
        "uq_webhook_connection_event", "webhook_events", ["connection_id", "external_event_id"]
    )
    op.create_index("ix_webhook_events_connection_id", "webhook_events", ["connection_id"])
    op.create_index(
        "ix_webhook_claim", "webhook_events", ["status", "next_attempt_at", "received_at"]
    )
    op.create_table(
        "provider_projections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("external_ref", sa.String(160), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("safe_data", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "provider", "resource_type", "external_ref"),
    )
    for table in ("provider_connections", "provider_projections"):
        _rls(table)


def downgrade() -> None:
    op.drop_table("provider_projections")
    op.drop_index("ix_webhook_claim", table_name="webhook_events")
    op.drop_index("ix_webhook_events_connection_id", table_name="webhook_events")
    op.drop_constraint("uq_webhook_connection_event", "webhook_events", type_="unique")
    op.drop_constraint("fk_webhook_connection", "webhook_events", type_="foreignkey")
    op.alter_column("webhook_events", "status", type_=sa.String(10), existing_type=sa.String(20))
    for name in (
        "processed_at",
        "processing_started_at",
        "next_attempt_at",
        "safe_error",
        "occurred_at",
        "encrypted_payload",
        "topic",
        "connection_id",
    ):
        op.drop_column("webhook_events", name)
    op.add_column(
        "webhook_events",
        sa.Column("signature_valid", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_unique_constraint(
        "webhook_events_organization_id_provider_external_event_id_key",
        "webhook_events",
        ["organization_id", "provider", "external_event_id"],
    )
    op.drop_table("provider_connections")
