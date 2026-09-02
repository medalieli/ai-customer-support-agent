import os
from collections.abc import AsyncIterator
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_db_session
from app.api.v1.knowledge import upload_document
from app.core.config import Settings
from app.domain.models import DocumentVersion, IngestionStatus, Role
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.knowledge.ingestion import process_document_version
from app.knowledge.retrieval import Citation, retrieve_passages, validate_citation
from app.main import create_app
from app.seed import ORGANIZATIONS
from app.services.auth import Principal
from app.worker.main import ingest_document_version

pytestmark = pytest.mark.skipif(
    os.getenv("NOVACART_RUN_DB_TESTS") != "1", reason="integration database not requested"
)


class StubRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[object, ...]] = []

    async def enqueue_job(self, *args: object, **kwargs: object) -> object:
        self.jobs.append(args)
        return object()


@pytest.fixture
async def knowledge_client() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings, StubRedis]
]:
    settings = Settings(
        app_env="test",
        demo_auth_enabled=True,
        demo_staff_password=SecretStr("synthetic-demo-password"),
        chunk_size_words=30,
        chunk_overlap_words=5,
    )
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings)
    redis = StubRedis()
    app.state.redis = redis

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        login = await client.post(
            "/api/v1/auth/staff-login",
            json={
                "organization_slug": "novacart",
                "email": "admin@novacart.test",
                "password": "synthetic-demo-password",
            },
        )
        assert login.status_code == 200
        yield client, factory, settings, redis
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_lifecycle_hybrid_retrieval_and_citation_validation(
    knowledge_client: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings, StubRedis],
) -> None:
    client, factory, settings, redis = knowledge_client
    slug = f"test-shipping-{uuid4()}"
    first_content = b"# Address changes\nUnfulfilled parcels can change address before shipment."
    upload = await client.post(
        "/api/v1/knowledge/documents",
        data={
            "slug": slug,
            "title": "Test Shipping Policy",
            "document_type": "shipping",
            "version": "1.0",
            "language": "en",
        },
        files={"file": ("shipping.md", first_content, "text/markdown")},
    )
    assert upload.status_code == 202
    version_id = UUID(upload.json()["id"])
    assert redis.jobs[-1][0] == "ingest_document_version"
    duplicate = await client.post(
        "/api/v1/knowledge/documents",
        data={
            "slug": slug,
            "title": "Test Shipping Policy",
            "document_type": "shipping",
            "version": "1.0",
            "language": "en",
        },
        files={"file": ("shipping.md", first_content, "text/markdown")},
    )
    assert duplicate.json()["id"] == str(version_id)
    async with factory() as session:
        result = await process_document_version(session, settings, ORGANIZATIONS[0].id, version_id)
        assert result["status"] == "ready"
        repeat = await process_document_version(session, settings, ORGANIZATIONS[0].id, version_id)
        assert repeat["idempotent"] is True
        assert await process_document_version(session, settings, ORGANIZATIONS[0].id, uuid4()) == {
            "status": "not_found"
        }
        passages = await retrieve_passages(
            session, settings, ORGANIZATIONS[0].id, "change address unfulfilled", top_k=5
        )
        matching = next(item for item in passages if item.citation.version_id == version_id)
        assert matching.lexical_score > 0
        assert matching.vector_score != 0
        assert matching.fusion_score > 0
        assert matching.rerank_score > 0
        assert await validate_citation(session, ORGANIZATIONS[0].id, matching.citation)
        assert not await validate_citation(
            session, ORGANIZATIONS[0].id, replace(matching.citation, snippet="fabricated")
        )
        assert not await validate_citation(
            session,
            ORGANIZATIONS[1].id,
            Citation(
                matching.citation.record_id,
                matching.citation.document_id,
                matching.citation.version_id,
                matching.citation.chunk_id,
                matching.citation.source_title,
                matching.citation.language,
                matching.citation.section,
                matching.citation.page,
                matching.citation.snippet,
            ),
        )

    listed = await client.get("/api/v1/knowledge/documents")
    assert any(item["slug"] == slug for item in listed.json())
    status = await client.get(
        f"/api/v1/knowledge/documents/{matching.citation.document_id}/versions/{version_id}"
    )
    assert status.json()["status"] == "ready"
    missing_status = await client.get(
        f"/api/v1/knowledge/documents/{matching.citation.document_id}/versions/{uuid4()}"
    )
    assert missing_status.status_code == 404
    retry_ready = await client.post(
        f"/api/v1/knowledge/documents/{matching.citation.document_id}/versions/{version_id}/retry"
    )
    assert retry_ready.status_code == 404
    search = await client.get(
        "/api/v1/knowledge/search",
        params={
            "query": "change address unfulfilled",
            "language": "en",
            "document_type": "shipping",
            "top_k": 5,
        },
    )
    assert search.status_code == 200
    citation_payload = next(
        item["citation"]
        for item in search.json()
        if item["citation"]["version_id"] == str(version_id)
    )
    assert (
        await client.post("/api/v1/knowledge/citations/validate", json=citation_payload)
    ).json() == {"valid": True}

    replacement = await client.post(
        "/api/v1/knowledge/documents",
        data={
            "slug": slug,
            "title": "Test Shipping Policy",
            "document_type": "shipping",
            "version": "2.0",
            "language": "en",
        },
        files={
            "file": (
                "shipping.md",
                b"# Address changes\nA new active replacement mentions dispatch lock.",
                "text/markdown",
            )
        },
    )
    replacement_id = UUID(replacement.json()["id"])
    async with factory() as session:
        await process_document_version(session, settings, ORGANIZATIONS[0].id, replacement_id)
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        old = await session.get(DocumentVersion, version_id)
        assert old is not None and old.status == IngestionStatus.SUPERSEDED
    deleted = await client.delete(f"/api/v1/knowledge/documents/{matching.citation.document_id}")
    assert deleted.status_code == 204
    deleted_upload = await client.post(
        "/api/v1/knowledge/documents",
        data={
            "slug": slug,
            "title": "Deleted",
            "document_type": "shipping",
            "version": "3.0",
            "language": "en",
        },
        files={"file": ("shipping.md", b"new content", "text/markdown")},
    )
    assert deleted_upload.status_code == 403
    excluded = await client.get(
        "/api/v1/knowledge/search", params={"query": "dispatch lock", "top_k": 20}
    )
    assert all(
        item["citation"]["document_id"] != str(matching.citation.document_id)
        for item in excluded.json()
    )


@pytest.mark.asyncio
async def test_failed_ingestion_retry_worker_and_permissions(
    knowledge_client: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings, StubRedis],
) -> None:
    client, factory, settings, redis = knowledge_client
    invalid_file = await client.post(
        "/api/v1/knowledge/documents",
        data={
            "slug": "bad-file",
            "title": "Bad file",
            "document_type": "product",
            "version": "1.0",
            "language": "en",
        },
        files={"file": ("bad.exe", b"bad", "application/octet-stream")},
    )
    assert invalid_file.status_code == 422
    upload = await client.post(
        "/api/v1/knowledge/documents",
        data={
            "slug": f"failed-{uuid4()}",
            "title": "Failure Fixture",
            "document_type": "product",
            "version": "1.0",
            "language": "en",
        },
        files={"file": ("failure.txt", b"[[EMBEDDING_FAILURE]]", "text/plain")},
    )
    version_id = UUID(upload.json()["id"])
    document_id: UUID
    async with factory() as session:
        result = await process_document_version(session, settings, ORGANIZATIONS[0].id, version_id)
        assert result == {"status": "failed", "error_code": "ingestion_failed"}
        await set_tenant_scope(session, ORGANIZATIONS[0].id)
        item = await session.get(DocumentVersion, version_id)
        assert item is not None
        document_id = item.document_id
    retry = await client.post(
        f"/api/v1/knowledge/documents/{document_id}/versions/{version_id}/retry"
    )
    assert retry.status_code == 202 and redis.jobs
    worker_result = await ingest_document_version({}, str(ORGANIZATIONS[0].id), str(version_id))
    assert worker_result["status"] == "failed"

    await client.post("/api/v1/auth/logout")
    support_login = await client.post(
        "/api/v1/auth/staff-login",
        json={
            "organization_slug": "novacart",
            "email": "support@novacart.test",
            "password": "synthetic-demo-password",
        },
    )
    assert support_login.status_code == 200
    denied = await client.get("/api/v1/knowledge/documents")
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_upload_boundary_direct_call(
    knowledge_client: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings, StubRedis],
) -> None:
    _, factory, _, redis = knowledge_client
    principal = Principal(
        "staff",
        ORGANIZATIONS[0].id,
        UUID("10000000-0000-0000-0000-000000000102"),
        Role.ADMIN,
        uuid4(),
    )
    request = cast(Any, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis))))
    slug = f"direct-{uuid4()}"
    async with factory() as session:
        response = await upload_document(
            request,
            principal,
            session,
            UploadFile(
                file=BytesIO(b"# Direct\nDirect upload boundary text."), filename="direct.md"
            ),
            slug,
            "Direct Boundary",
            "product",
            "1.0",
            "en",
        )
        replay = await upload_document(
            request,
            principal,
            session,
            UploadFile(
                file=BytesIO(b"# Direct\nDirect upload boundary text."), filename="direct.md"
            ),
            slug,
            "Direct Boundary",
            "product",
            "1.0",
            "en",
        )
    assert response.status == "pending"
    assert replay.id == response.id
    assert len(redis.jobs) == 2
