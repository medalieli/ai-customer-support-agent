import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.domain.models import (
    Customer,
    Organization,
    OrganizationMembership,
    ProviderConnection,
    Role,
    StaffUser,
)
from app.infrastructure.database import create_database_engine, set_tenant_scope
from app.services.security import hash_password, verify_password
from app.services.webhooks import encrypt


@dataclass(frozen=True)
class OrganizationSeed:
    id: UUID
    slug: str
    name: str


ORGANIZATIONS = (
    OrganizationSeed(UUID("10000000-0000-0000-0000-000000000001"), "novacart", "NovaCart"),
    OrganizationSeed(UUID("20000000-0000-0000-0000-000000000001"), "orbit-outlet", "Orbit Outlet"),
)

STAFF = (
    (
        UUID("10000000-0000-0000-0000-000000000101"),
        "support@novacart.test",
        "Nova Support",
        0,
        Role.SUPPORT,
    ),
    (
        UUID("10000000-0000-0000-0000-000000000102"),
        "admin@novacart.test",
        "Nova Admin",
        0,
        Role.ADMIN,
    ),
    (
        UUID("20000000-0000-0000-0000-000000000101"),
        "support@orbit.test",
        "Orbit Support",
        1,
        Role.SUPPORT,
    ),
    (
        UUID("20000000-0000-0000-0000-000000000102"),
        "admin@orbit.test",
        "Orbit Admin",
        1,
        Role.ADMIN,
    ),
)

CUSTOMERS = (
    (
        UUID("10000000-0000-0000-0000-000000000201"),
        0,
        "amira-en",
        "Amira Haddad",
        "amira@synthetic.test",
        "en",
    ),
    (
        UUID("10000000-0000-0000-0000-000000000202"),
        0,
        "lucas-fr",
        "Lucas Martin",
        "lucas@synthetic.test",
        "fr",
    ),
    (
        UUID("20000000-0000-0000-0000-000000000201"),
        1,
        "nora-en",
        "Nora Silva",
        "nora@orbit.synthetic.test",
        "en",
    ),
)


async def seed(session: AsyncSession, password: str) -> None:
    settings = get_settings()
    for item in ORGANIZATIONS:
        if await session.get(Organization, item.id) is None:
            session.add(Organization(id=item.id, slug=item.slug, name=item.name))
    await session.commit()

    for staff_id, email, name, _, _ in STAFF:
        existing_staff = await session.get(StaffUser, staff_id)
        if existing_staff is None:
            session.add(
                StaffUser(
                    id=staff_id,
                    email=email,
                    display_name=name,
                    password_hash=hash_password(password),
                )
            )
        elif not verify_password(existing_staff.password_hash, password):
            existing_staff.password_hash = hash_password(password)
    await session.commit()

    for organization_index, organization in enumerate(ORGANIZATIONS):
        await set_tenant_scope(session, organization.id)
        for customer_id, org_index, key, name, email, locale in CUSTOMERS:
            if org_index == organization_index and await session.get(Customer, customer_id) is None:
                session.add(
                    Customer(
                        id=customer_id,
                        organization_id=organization.id,
                        demo_key=key,
                        display_name=name,
                        email=email,
                        locale=locale,
                        provider_customer_ref=f"seed-{key}",
                    )
                )
        for staff_id, _, _, org_index, role in STAFF:
            if org_index != organization_index:
                continue
            membership = await session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization.id,
                    OrganizationMembership.staff_user_id == staff_id,
                )
            )
            if membership is None:
                session.add(
                    OrganizationMembership(
                        organization_id=organization.id, staff_user_id=staff_id, role=role
                    )
                )
        for provider, endpoint_key, secret in (
            (
                "mock_commerce",
                f"{organization.slug}-commerce",
                settings.mock_commerce_webhook_secret,
            ),
            ("mock_crm", f"{organization.slug}-crm", settings.mock_crm_webhook_secret),
        ):
            connection = await session.scalar(
                select(ProviderConnection).where(
                    ProviderConnection.organization_id == organization.id,
                    ProviderConnection.provider == provider,
                )
            )
            if connection is None:
                session.add(
                    ProviderConnection(
                        organization_id=organization.id,
                        provider=provider,
                        endpoint_key=endpoint_key,
                        encrypted_current_secret=encrypt(
                            settings, secret.get_secret_value().encode()
                        ),
                        active=True,
                    )
                )
        await session.commit()


async def main() -> None:
    settings = get_settings()
    if not settings.demo_auth_enabled or settings.demo_staff_password is None:
        raise RuntimeError("seed requires explicitly enabled demo authentication and password")
    engine = create_database_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session, settings.demo_staff_password.get_secret_value())
    await engine.dispose()
    print("synthetic seed data ready")


if __name__ == "__main__":
    asyncio.run(main())
