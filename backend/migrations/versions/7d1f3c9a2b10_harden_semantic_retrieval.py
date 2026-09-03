"""harden semantic retrieval providers

Revision ID: 7d1f3c9a2b10
Revises: cca2768466e7
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "7d1f3c9a2b10"
down_revision: str | None = "cca2768466e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_chunk_metadata_embedding_hnsw", table_name="chunk_metadata")
    op.drop_column("chunk_metadata", "embedding")
    op.add_column("chunk_metadata", sa.Column("embedding", Vector(1536), nullable=True))
    op.add_column(
        "chunk_metadata", sa.Column("indexing_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_chunk_metadata_indexing_fingerprint"),
        "chunk_metadata",
        ["indexing_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_chunk_metadata_embedding_hnsw",
        "chunk_metadata",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.add_column(
        "document_versions", sa.Column("embedding_provider", sa.String(length=40), nullable=True)
    )
    op.add_column(
        "document_versions", sa.Column("embedding_model", sa.String(length=160), nullable=True)
    )
    op.add_column(
        "document_versions", sa.Column("embedding_dimension", sa.Integer(), nullable=True)
    )
    op.add_column(
        "document_versions", sa.Column("indexing_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_document_versions_indexing_fingerprint"),
        "document_versions",
        ["indexing_fingerprint"],
        unique=False,
    )
    # A 32-dimensional vector cannot be meaningfully padded into the new semantic space.
    # Preserve source/chunk/citation history but make every prior index ineligible until reingested.
    op.execute(
        "UPDATE document_versions SET status = 'PENDING', approved_at = NULL "
        "WHERE status IN ('READY', 'PROCESSING')"
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_metadata_embedding_hnsw", table_name="chunk_metadata")
    op.drop_index(op.f("ix_chunk_metadata_indexing_fingerprint"), table_name="chunk_metadata")
    op.drop_column("chunk_metadata", "indexing_fingerprint")
    op.drop_column("chunk_metadata", "embedding")
    op.add_column("chunk_metadata", sa.Column("embedding", Vector(32), nullable=True))
    op.create_index(
        "ix_chunk_metadata_embedding_hnsw",
        "chunk_metadata",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_document_versions_indexing_fingerprint"), table_name="document_versions")
    op.drop_column("document_versions", "indexing_fingerprint")
    op.drop_column("document_versions", "embedding_dimension")
    op.drop_column("document_versions", "embedding_model")
    op.drop_column("document_versions", "embedding_provider")
    op.execute(
        "UPDATE document_versions SET status = 'PENDING', approved_at = NULL "
        "WHERE status IN ('READY', 'PROCESSING')"
    )
