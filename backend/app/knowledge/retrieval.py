import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import (
    ChunkMetadata,
    CitationRecord,
    DocumentStatus,
    DocumentVersion,
    IngestionStatus,
    KnowledgeDocument,
)
from app.infrastructure.database import set_tenant_scope
from app.knowledge.embeddings import DeterministicFakeEmbeddings, EmbeddingProvider

RRF_K = 60
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "do",
    "does",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "or",
    "the",
    "this",
    "to",
    "what",
    "when",
    "with",
    "you",
    "au",
    "aux",
    "avec",
    "ce",
    "comment",
    "dans",
    "de",
    "des",
    "du",
    "est",
    "et",
    "faire",
    "la",
    "le",
    "les",
    "ma",
    "mon",
    "pour",
    "que",
    "quel",
    "quelle",
    "un",
    "une",
}
TRANSLATIONS = {
    "livraison": "shipping delivery",
    "retour": "return refund",
    "remboursement": "refund return",
    "garantie": "warranty",
    "endommage": "damaged",
    "confidentialite": "privacy",
    "compte": "account",
    "retard": "delayed delay",
    "shipping": "livraison",
    "return": "retour remboursement",
    "refund": "remboursement retour",
    "warranty": "garantie",
    "damaged": "endommage",
    "privacy": "confidentialite",
}


@dataclass(frozen=True)
class Citation:
    record_id: UUID
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    source_title: str
    language: str
    section: str | None
    page: int | None
    snippet: str


@dataclass(frozen=True)
class Passage:
    text: str
    lexical_score: float
    vector_score: float
    fusion_score: float
    rerank_score: float
    citation: Citation


def normalized_tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    tokens = set(re.findall(r"\w+", normalized)) - STOPWORDS
    expanded = set(tokens)
    for token in tokens:
        expanded.update(TRANSLATIONS.get(token, "").split())
    return expanded


def local_rerank(query: str, text: str) -> float:
    query_tokens = normalized_tokens(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & normalized_tokens(text)) / len(query_tokens)


async def retrieve_passages(
    session: AsyncSession,
    settings: Settings,
    organization_id: UUID,
    query: str,
    *,
    language: str | None = None,
    document_type: str | None = None,
    top_k: int | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> list[Passage]:
    await set_tenant_scope(session, organization_id)
    provider = embedding_provider or DeterministicFakeEmbeddings(settings.embedding_dimensions)
    query_embedding = (await provider.embed([query]))[0]
    filters = [
        ChunkMetadata.organization_id == organization_id,
        KnowledgeDocument.organization_id == organization_id,
        KnowledgeDocument.status == DocumentStatus.APPROVED,
        KnowledgeDocument.deleted_at.is_(None),
        DocumentVersion.status == IngestionStatus.READY,
    ]
    if language:
        filters.append(ChunkMetadata.language == language)
    if document_type:
        filters.append(KnowledgeDocument.document_type == document_type)
    joined = (
        select(ChunkMetadata, DocumentVersion, KnowledgeDocument)
        .join(
            DocumentVersion,
            (DocumentVersion.id == ChunkMetadata.document_version_id)
            & (DocumentVersion.organization_id == ChunkMetadata.organization_id),
        )
        .join(
            KnowledgeDocument,
            (KnowledgeDocument.id == DocumentVersion.document_id)
            & (KnowledgeDocument.organization_id == DocumentVersion.organization_id),
        )
        .where(*filters)
    )
    lexical_rank = func.ts_rank_cd(
        ChunkMetadata.search_vector, func.plainto_tsquery("simple", query)
    )
    lexical_rows = (
        await session.execute(
            joined.add_columns(lexical_rank.label("score"))
            .where(ChunkMetadata.search_vector.op("@@")(func.plainto_tsquery("simple", query)))
            .order_by(desc("score"))
            .limit(settings.retrieval_lexical_k)
        )
    ).all()
    distance = ChunkMetadata.embedding.cosine_distance(query_embedding)
    vector_rows = (
        await session.execute(
            joined.add_columns(distance.label("distance"))
            .order_by(distance)
            .limit(settings.retrieval_vector_k)
        )
    ).all()
    candidates: dict[UUID, dict[str, Any]] = {}
    for rank, (chunk, version, document, score) in enumerate(lexical_rows, start=1):
        candidates[chunk.id] = {
            "chunk": chunk,
            "version": version,
            "document": document,
            "lexical": float(score),
            "vector": 0.0,
            "fusion": 1 / (RRF_K + rank),
        }
    for rank, (chunk, version, document, raw_distance) in enumerate(vector_rows, start=1):
        item = candidates.setdefault(
            chunk.id,
            {
                "chunk": chunk,
                "version": version,
                "document": document,
                "lexical": 0.0,
                "vector": 0.0,
                "fusion": 0.0,
            },
        )
        item["vector"] = 1.0 - float(raw_distance)
        item["fusion"] += 1 / (RRF_K + rank)
    supported = [
        item
        for item in candidates.values()
        if local_rerank(query, item["chunk"].text) > 0 or item["lexical"] > 0
    ]
    ranked = sorted(
        supported,
        key=lambda item: (local_rerank(query, item["chunk"].text), item["fusion"]),
        reverse=True,
    )[: top_k or settings.retrieval_top_k]
    results: list[Passage] = []
    for item in ranked:
        result_chunk: ChunkMetadata = item["chunk"]
        result_version: DocumentVersion = item["version"]
        result_document: KnowledgeDocument = item["document"]
        snippet = result_chunk.text
        record = CitationRecord(
            organization_id=organization_id,
            document_id=result_document.id,
            document_version_id=result_version.id,
            chunk_id=result_chunk.id,
            source_title=result_document.title,
            language=result_chunk.language,
            section_anchor=result_chunk.section_anchor,
            page_number=result_chunk.page_number,
            snippet=snippet,
            chunk_checksum=result_chunk.checksum,
        )
        session.add(record)
        await session.flush()
        results.append(
            Passage(
                text=result_chunk.text,
                lexical_score=item["lexical"],
                vector_score=item["vector"],
                fusion_score=item["fusion"],
                rerank_score=local_rerank(query, result_chunk.text),
                citation=Citation(
                    record.id,
                    result_document.id,
                    result_version.id,
                    result_chunk.id,
                    result_document.title,
                    result_chunk.language,
                    result_chunk.section_anchor,
                    result_chunk.page_number,
                    snippet,
                ),
            )
        )
    await session.commit()
    return results


async def validate_citation(
    session: AsyncSession, organization_id: UUID, citation: Citation
) -> bool:
    await set_tenant_scope(session, organization_id)
    row = await session.execute(
        select(CitationRecord, ChunkMetadata, DocumentVersion, KnowledgeDocument)
        .join(
            ChunkMetadata,
            (ChunkMetadata.id == CitationRecord.chunk_id)
            & (ChunkMetadata.organization_id == CitationRecord.organization_id),
        )
        .join(
            DocumentVersion,
            (DocumentVersion.id == CitationRecord.document_version_id)
            & (DocumentVersion.organization_id == CitationRecord.organization_id),
        )
        .join(
            KnowledgeDocument,
            (KnowledgeDocument.id == CitationRecord.document_id)
            & (KnowledgeDocument.organization_id == CitationRecord.organization_id),
        )
        .where(
            CitationRecord.organization_id == organization_id,
            CitationRecord.id == citation.record_id,
        )
    )
    value = row.one_or_none()
    if value is None:
        return False
    record, chunk, version, document = value
    return (
        record.document_id == citation.document_id
        and record.document_version_id == citation.version_id
        and record.chunk_id == citation.chunk_id
        and version.document_id == document.id
        and chunk.document_version_id == version.id
        and record.source_title == citation.source_title == document.title
        and record.language == citation.language == chunk.language
        and record.section_anchor == citation.section == chunk.section_anchor
        and record.page_number == citation.page == chunk.page_number
        and record.snippet == citation.snippet
        and citation.snippet in chunk.text
        and record.chunk_checksum == chunk.checksum
    )
