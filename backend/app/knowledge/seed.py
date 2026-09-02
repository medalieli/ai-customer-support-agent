import asyncio
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.domain.models import (
    DocumentStatus,
    DocumentVersion,
    IngestionStatus,
    KnowledgeDocument,
)
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.knowledge.ingestion import content_checksum, process_document_version

ROOT = Path(__file__).resolve().parents[2] / "knowledge-base"
TENANTS = {
    "novacart": UUID("10000000-0000-0000-0000-000000000001"),
    "orbit-outlet": UUID("20000000-0000-0000-0000-000000000001"),
}
DOCUMENT_TYPES = {
    "shipping": "shipping",
    "returns_refunds": "returns_refunds",
    "warranty": "warranty",
    "damaged_item": "damaged_item",
    "products": "product",
    "privacy_account": "privacy_account",
}
TITLES = {
    "shipping": "Shipping and Delivery Policy",
    "returns_refunds": "Return and Refund Policy",
    "warranty": "Limited Warranty",
    "damaged_item": "Damaged Item Procedure",
    "products": "NovaCart Product Guide",
    "privacy_account": "Privacy and Account Procedure",
}


async def seed_documents() -> int:
    settings = get_settings()
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    processed = 0
    try:
        async with factory() as session:
            for tenant_slug, organization_id in TENANTS.items():
                tenant_root = ROOT / tenant_slug
                if not tenant_root.exists():
                    continue
                await set_tenant_scope(session, organization_id)
                for path in sorted(tenant_root.glob("*/*")):
                    language = path.parent.name
                    slug = path.stem
                    content = path.read_bytes()
                    document_id = uuid5(NAMESPACE_URL, f"{tenant_slug}:{slug}")
                    checksum = content_checksum(content)
                    version_id = uuid5(
                        NAMESPACE_URL, f"{tenant_slug}:{slug}:{language}:1.0:{checksum}"
                    )
                    document = await session.get(KnowledgeDocument, document_id)
                    if document is None:
                        document = KnowledgeDocument(
                            id=document_id,
                            organization_id=organization_id,
                            slug=slug,
                            title=TITLES.get(slug, f"{tenant_slug} {slug}"),
                            document_type=DOCUMENT_TYPES[slug],
                            status=DocumentStatus.APPROVED,
                        )
                        session.add(document)
                        await session.flush()
                    else:
                        expected_title = TITLES.get(slug, f"{tenant_slug} {slug}")
                        if document.title != expected_title:
                            document.title = expected_title
                            await session.commit()
                    version = await session.scalar(
                        select(DocumentVersion).where(
                            DocumentVersion.organization_id == organization_id,
                            DocumentVersion.document_id == document.id,
                            DocumentVersion.locale == language,
                            DocumentVersion.checksum == checksum,
                        )
                    )
                    if version is None:
                        version = DocumentVersion(
                            id=version_id,
                            organization_id=organization_id,
                            document_id=document.id,
                            version=f"1.0-{checksum[:8]}",
                            locale=language,
                            checksum=checksum,
                            status=IngestionStatus.PENDING,
                            source_filename=path.name,
                            media_type=("text/markdown" if path.suffix == ".md" else "text/plain"),
                            raw_content=content,
                        )
                        session.add(version)
                        await session.commit()
                    elif version.status != IngestionStatus.READY and not version.raw_content:
                        # An M4 downgrade intentionally preserves M2 metadata rows. Restore the
                        # committed synthetic source before retrying that legacy pending version.
                        version.source_filename = path.name
                        version.media_type = (
                            "text/markdown" if path.suffix == ".md" else "text/plain"
                        )
                        version.raw_content = content
                        version.status = IngestionStatus.PENDING
                        version.error_code = None
                        await session.commit()
                    result = await process_document_version(
                        session, settings, organization_id, version.id
                    )
                    if result["status"] == "ready":
                        processed += 1
    finally:
        await engine.dispose()
    return processed


async def main() -> None:
    count = await seed_documents()
    print(f"knowledge documents ready: {count}")


if __name__ == "__main__":
    asyncio.run(main())
