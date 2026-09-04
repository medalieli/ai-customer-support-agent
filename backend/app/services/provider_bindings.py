from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import ProviderConnection, ProviderResourceBinding


def provider_name(settings: Settings, resource_type: str) -> str:
    if resource_type == "order":
        return "mock_commerce" if settings.commerce_provider == "mock" else "shopify"
    if resource_type == "ticket":
        return "mock_crm" if settings.crm_provider == "mock" else "hubspot"
    raise ValueError("unsupported resource type")


async def bind_resource(
    session: AsyncSession,
    settings: Settings,
    *,
    organization_id: UUID,
    customer_id: UUID,
    conversation_id: UUID,
    resource_type: str,
    external_ref: str,
    provider_customer_ref: str | None = None,
) -> ProviderResourceBinding:
    provider = provider_name(settings, resource_type)
    connection = await session.scalar(
        select(ProviderConnection)
        .where(
            ProviderConnection.organization_id == organization_id,
            ProviderConnection.provider == provider,
            ProviderConnection.active.is_(True),
        )
        .order_by(ProviderConnection.created_at)
        .limit(1)
    )
    if connection is None:
        raise RuntimeError("provider_connection_missing")
    existing = await session.scalar(
        select(ProviderResourceBinding).where(
            ProviderResourceBinding.organization_id == organization_id,
            ProviderResourceBinding.connection_id == connection.id,
            ProviderResourceBinding.resource_type == resource_type,
            ProviderResourceBinding.external_ref == external_ref,
            ProviderResourceBinding.conversation_id == conversation_id,
        )
    )
    if existing:
        return existing
    value = ProviderResourceBinding(
        organization_id=organization_id,
        connection_id=connection.id,
        resource_type=resource_type,
        external_ref=external_ref,
        customer_id=customer_id,
        conversation_id=conversation_id,
        provider_customer_ref=provider_customer_ref,
    )
    session.add(value)
    await session.flush()
    return value
