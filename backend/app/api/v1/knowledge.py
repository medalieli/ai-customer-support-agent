from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import CurrentPrincipal, DatabaseSession, RequestSettings
from app.domain.models import (
    DocumentStatus,
    DocumentVersion,
    IngestionStatus,
    KnowledgeDocument,
)
from app.infrastructure.database import set_tenant_scope
from app.knowledge.extraction import FileValidationError, validate_file
from app.knowledge.ingestion import content_checksum
from app.knowledge.retrieval import Citation, retrieve_passages, validate_citation
from app.services.auth import AuthorizationError, ResourceNotFoundError, require_admin

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
DOCUMENT_TYPES = {
    "shipping",
    "returns_refunds",
    "warranty",
    "damaged_item",
    "product",
    "privacy_account",
}


class VersionResponse(BaseModel):
    id: UUID
    document_id: UUID
    version: str
    language: str
    status: str
    filename: str
    checksum: str
    error_code: str | None
    created_at: datetime


class DocumentResponse(BaseModel):
    id: UUID
    slug: str
    title: str
    document_type: str
    status: str
    versions: list[VersionResponse]


class CitationResponse(BaseModel):
    record_id: UUID
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    source_title: str
    language: str
    section: str | None
    page: int | None
    snippet: str


class PassageResponse(BaseModel):
    text: str
    lexical_score: float
    vector_score: float
    fusion_score: float
    rerank_score: float
    citation: CitationResponse


class CitationValidationRequest(CitationResponse):
    pass


def ensure_admin(principal: CurrentPrincipal) -> None:
    require_admin(principal)


async def queue_ingestion(request: Request, organization_id: UUID, version_id: UUID) -> None:
    queue = getattr(request.app.state, "job_queue", request.app.state.redis)
    await queue.enqueue_job(
        "ingest_document_version",
        str(organization_id),
        str(version_id),
        _job_id=f"knowledge:{organization_id}:{version_id}",
    )


def version_response(version: DocumentVersion) -> VersionResponse:
    return VersionResponse(
        id=version.id,
        document_id=version.document_id,
        version=version.version,
        language=version.locale,
        status=version.status.value,
        filename=version.source_filename,
        checksum=version.checksum,
        error_code=version.error_code,
        created_at=version.created_at,
    )


@router.post("/documents", response_model=VersionResponse, status_code=202)
async def upload_document(
    request: Request,
    principal: CurrentPrincipal,
    session: DatabaseSession,
    file: Annotated[UploadFile, File()],
    slug: Annotated[str, Form(min_length=1, max_length=160)],
    title: Annotated[str, Form(min_length=1, max_length=240)],
    document_type: Annotated[str, Form()],
    version: Annotated[str, Form(min_length=1, max_length=80)],
    language: Annotated[str, Form(pattern="^(en|fr)$")],
) -> VersionResponse:
    ensure_admin(principal)
    if document_type not in DOCUMENT_TYPES:
        raise FileValidationError("unsupported_document_type")
    content = await file.read()
    validate_file(file.filename or "", content)
    checksum = content_checksum(content)
    await set_tenant_scope(session, principal.organization_id)
    document = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.organization_id == principal.organization_id,
            KnowledgeDocument.slug == slug,
        )
    )
    if document is None:
        document = KnowledgeDocument(
            organization_id=principal.organization_id,
            slug=slug,
            title=title,
            document_type=document_type,
            status=DocumentStatus.APPROVED,
        )
        session.add(document)
        await session.flush()
    elif document.deleted_at is not None:
        raise AuthorizationError
    existing = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == principal.organization_id,
            DocumentVersion.document_id == document.id,
            DocumentVersion.locale == language,
            DocumentVersion.checksum == checksum,
        )
    )
    if existing is not None:
        if existing.status in {IngestionStatus.PENDING, IngestionStatus.PROCESSING}:
            await queue_ingestion(request, principal.organization_id, existing.id)
        return version_response(existing)
    item = DocumentVersion(
        organization_id=principal.organization_id,
        document_id=document.id,
        version=version,
        locale=language,
        checksum=checksum,
        status=IngestionStatus.PENDING,
        source_filename=file.filename or "document.txt",
        media_type=file.content_type or "application/octet-stream",
        raw_content=content,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    await queue_ingestion(request, principal.organization_id, item.id)
    return version_response(item)


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(
    principal: CurrentPrincipal, session: DatabaseSession
) -> list[DocumentResponse]:
    ensure_admin(principal)
    await set_tenant_scope(session, principal.organization_id)
    documents = list(
        await session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.organization_id == principal.organization_id)
            .order_by(KnowledgeDocument.slug)
        )
    )
    results: list[DocumentResponse] = []
    for document in documents:
        versions = list(
            await session.scalars(
                select(DocumentVersion)
                .where(
                    DocumentVersion.organization_id == principal.organization_id,
                    DocumentVersion.document_id == document.id,
                )
                .order_by(DocumentVersion.created_at.desc())
            )
        )
        results.append(
            DocumentResponse(
                id=document.id,
                slug=document.slug,
                title=document.title,
                document_type=document.document_type,
                status=document.status.value,
                versions=[version_response(item) for item in versions],
            )
        )
    return results


@router.get("/documents/{document_id}/versions/{version_id}", response_model=VersionResponse)
async def ingestion_status(
    document_id: UUID,
    version_id: UUID,
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> VersionResponse:
    ensure_admin(principal)
    await set_tenant_scope(session, principal.organization_id)
    item = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == principal.organization_id,
            DocumentVersion.document_id == document_id,
            DocumentVersion.id == version_id,
        )
    )
    if item is None:
        raise ResourceNotFoundError
    return version_response(item)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID, principal: CurrentPrincipal, session: DatabaseSession
) -> None:
    ensure_admin(principal)
    await set_tenant_scope(session, principal.organization_id)
    document = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.organization_id == principal.organization_id,
            KnowledgeDocument.id == document_id,
        )
    )
    if document is None:
        raise ResourceNotFoundError
    document.status = DocumentStatus.ARCHIVED
    document.deleted_at = datetime.now().astimezone()
    await session.commit()


@router.post("/documents/{document_id}/versions/{version_id}/retry", status_code=202)
async def retry_ingestion(
    request: Request,
    document_id: UUID,
    version_id: UUID,
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> dict[str, str]:
    ensure_admin(principal)
    await set_tenant_scope(session, principal.organization_id)
    item = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == principal.organization_id,
            DocumentVersion.document_id == document_id,
            DocumentVersion.id == version_id,
            DocumentVersion.status == IngestionStatus.FAILED,
        )
    )
    if item is None:
        raise ResourceNotFoundError
    item.status = IngestionStatus.PENDING
    item.error_code = None
    await session.commit()
    await queue_ingestion(request, principal.organization_id, item.id)
    return {"status": "pending"}


@router.get("/search", response_model=list[PassageResponse])
async def search_knowledge(
    principal: CurrentPrincipal,
    session: DatabaseSession,
    settings: RequestSettings,
    query: str = Query(min_length=2, max_length=500),
    language: str | None = Query(default=None, pattern="^(en|fr)$"),
    document_type: str | None = None,
    top_k: int | None = Query(default=None, ge=1, le=20),
) -> list[PassageResponse]:
    passages = await retrieve_passages(
        session,
        settings,
        principal.organization_id,
        query,
        language=language,
        document_type=document_type,
        top_k=top_k,
    )
    return [PassageResponse.model_validate(item, from_attributes=True) for item in passages]


@router.post("/citations/validate")
async def citation_validation(
    payload: CitationValidationRequest,
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> dict[str, bool]:
    citation = Citation(
        payload.record_id,
        payload.document_id,
        payload.version_id,
        payload.chunk_id,
        payload.source_title,
        payload.language,
        payload.section,
        payload.page,
        payload.snippet,
    )
    return {"valid": await validate_citation(session, principal.organization_id, citation)}
