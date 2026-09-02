"""Enable the pgvector extension only; M2 owns business tables.

Revision ID: 20260902_0001
Revises: None
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN "
        "RAISE EXCEPTION 'required pgvector extension is unavailable'; "
        "END IF; END $$"
    )


def downgrade() -> None:
    # Shared extensions are intentionally retained to avoid destructive downgrades.
    pass
