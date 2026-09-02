import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid5

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import (
    ChunkMetadata,
    DocumentVersion,
    IngestionStatus,
)
from app.infrastructure.database import set_tenant_scope
from app.knowledge.chunking import chunk_sections
from app.knowledge.embeddings import DeterministicFakeEmbeddings, EmbeddingProvider
from app.knowledge.extraction import FileValidationError, extract


async def process_document_version(
    session: AsyncSession,
    settings: Settings,
    organization_id: UUID,
    version_id: UUID,
    embedding_provider: EmbeddingProvider | None = None,
) -> dict[str, object]:
    await set_tenant_scope(session, organization_id)
    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.id == version_id,
        )
    )
    if version is None:
        return {"status": "not_found"}
    if version.status == IngestionStatus.READY:
        count = len(
            list(
                await session.scalars(
                    select(ChunkMetadata.id).where(
                        ChunkMetadata.organization_id == organization_id,
                        ChunkMetadata.document_version_id == version_id,
                    )
                )
            )
        )
        return {"status": "ready", "chunks": count, "idempotent": True}
    version.status = IngestionStatus.PROCESSING
    version.error_code = None
    await session.commit()
    try:
        sections = extract(version.source_filename, version.raw_content)
        chunks = chunk_sections(sections, settings.chunk_size_words, settings.chunk_overlap_words)
        provider = embedding_provider or DeterministicFakeEmbeddings(settings.embedding_dimensions)
        embeddings = await provider.embed([chunk.text for chunk in chunks])
        await set_tenant_scope(session, organization_id)
        await session.execute(
            delete(ChunkMetadata).where(
                ChunkMetadata.organization_id == organization_id,
                ChunkMetadata.document_version_id == version_id,
            )
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            session.add(
                ChunkMetadata(
                    id=uuid5(version.id, str(chunk.ordinal)),
                    organization_id=organization_id,
                    document_version_id=version.id,
                    ordinal=chunk.ordinal,
                    token_count=chunk.token_count,
                    checksum=chunk.checksum,
                    text=chunk.text,
                    language=version.locale,
                    page_number=chunk.page,
                    section_anchor=chunk.section,
                    embedding=embedding,
                    search_vector=func.to_tsvector("simple", chunk.text),
                    metadata_json={},
                )
            )
        await session.execute(
            update(DocumentVersion)
            .where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.document_id == version.document_id,
                DocumentVersion.locale == version.locale,
                DocumentVersion.id != version.id,
                DocumentVersion.status == IngestionStatus.READY,
            )
            .values(status=IngestionStatus.SUPERSEDED, effective_to=datetime.now(timezone.utc))
        )
        version.status = IngestionStatus.READY
        version.approved_at = datetime.now(timezone.utc)
        await session.commit()
        return {"status": "ready", "chunks": len(chunks), "idempotent": False}
    except (FileValidationError, RuntimeError, ValueError):
        await session.rollback()
        await set_tenant_scope(session, organization_id)
        failed = await session.get(DocumentVersion, version_id)
        if failed is not None:
            failed.status = IngestionStatus.FAILED
            failed.error_code = "ingestion_failed"
            await session.commit()
        return {"status": "failed", "error_code": "ingestion_failed"}


def content_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
