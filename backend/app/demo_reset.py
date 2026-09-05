"""Guarded reset for documented synthetic NovaCart demo operational data."""

import asyncio
import os

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.domain.models import Base
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.seed import ORGANIZATIONS, seed

RESET_TABLES = {
    "agent_events",
    "agent_runs",
    "agent_threads",
    "citation_records",
    "consent_records",
    "conversations",
    "customer_sessions",
    "messages",
    "pending_actions",
    "provider_projections",
    "provider_resource_bindings",
    "refund_decisions",
    "support_tickets",
    "tool_runs",
    "webhook_conversation_effects",
    "webhook_events",
}


async def main() -> None:
    settings = get_settings()
    if settings.app_env != "development" or not settings.demo_auth_enabled:
        raise RuntimeError("demo reset is allowed only in demo-enabled development")
    if os.getenv("NOVACART_CONFIRM_DEMO_RESET") != "RESET_SYNTHETIC_NOVACART":
        raise RuntimeError("set NOVACART_CONFIRM_DEMO_RESET=RESET_SYNTHETIC_NOVACART")
    if settings.demo_staff_password is None:
        raise RuntimeError("demo reset requires the synthetic staff password")

    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    organization_id = ORGANIZATIONS[0].id
    async with factory() as session:
        await set_tenant_scope(session, organization_id)
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in RESET_TABLES and "organization_id" in table.c:
                await session.execute(
                    delete(table).where(table.c.organization_id == organization_id)
                )
        await session.commit()
        await seed(session, settings.demo_staff_password.get_secret_value())
    await engine.dispose()
    print("NovaCart synthetic demo reset: 0 conversations, 0 tickets")


if __name__ == "__main__":
    asyncio.run(main())
