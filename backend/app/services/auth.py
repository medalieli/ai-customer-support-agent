from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.models import (
    Customer,
    CustomerSession,
    Organization,
    OrganizationMembership,
    Role,
    StaffSession,
    StaffUser,
    Status,
)
from app.infrastructure.database import set_tenant_scope
from app.services.audit import AuditService
from app.services.security import hash_session_token, new_session_secret, verify_password


@dataclass(frozen=True)
class Principal:
    kind: Literal["customer", "staff"]
    organization_id: UUID
    subject_id: UUID
    role: Role | None
    session_id: UUID


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


class ResourceNotFoundError(Exception):
    pass


def build_cookie_token(organization_id: UUID) -> str:
    return f"{organization_id}.{new_session_secret()}"


def organization_from_cookie(token: str) -> UUID:
    try:
        return UUID(token.split(".", 1)[0])
    except (ValueError, IndexError) as exc:
        raise AuthenticationError from exc


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditService(session)

    def _expiry(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=self.settings.session_ttl_seconds)

    async def demo_customer_login(
        self, organization_slug: str, persona_key: str
    ) -> tuple[str, Customer]:
        if not self.settings.demo_auth_enabled:
            raise AuthenticationError
        organization = await self.session.scalar(
            select(Organization).where(
                Organization.slug == organization_slug, Organization.status == Status.ACTIVE
            )
        )
        if organization is None:
            raise AuthenticationError
        await set_tenant_scope(self.session, organization.id)
        customer = await self.session.scalar(
            select(Customer).where(
                Customer.organization_id == organization.id,
                Customer.demo_key == persona_key,
                Customer.status == Status.ACTIVE,
            )
        )
        if customer is None:
            await self.audit.record(
                organization.id,
                "customer",
                "auth.login",
                "denied",
                reason_code="INVALID_CREDENTIALS",
                metadata={"method": "demo"},
            )
            await self.session.commit()
            raise AuthenticationError
        token = build_cookie_token(organization.id)
        self.session.add(
            CustomerSession(
                organization_id=organization.id,
                customer_id=customer.id,
                token_hash=hash_session_token(token),
                expires_at=self._expiry(),
            )
        )
        await self.audit.record(
            organization.id,
            "customer",
            "auth.login",
            "success",
            actor_id=customer.id,
            metadata={"method": "demo"},
        )
        await self.session.commit()
        return token, customer

    async def staff_login(
        self, organization_slug: str, email: str, password: str
    ) -> tuple[str, StaffUser, Role]:
        organization = await self.session.scalar(
            select(Organization).where(
                Organization.slug == organization_slug, Organization.status == Status.ACTIVE
            )
        )
        if organization is None:
            raise AuthenticationError
        await set_tenant_scope(self.session, organization.id)
        staff = await self.session.scalar(
            select(StaffUser).where(
                StaffUser.email == email.lower(), StaffUser.status == Status.ACTIVE
            )
        )
        if staff is None or not verify_password(staff.password_hash, password):
            await self.audit.record(
                organization.id,
                "staff",
                "auth.login",
                "denied",
                reason_code="INVALID_CREDENTIALS",
                metadata={"method": "password"},
            )
            await self.session.commit()
            raise AuthenticationError
        membership = await self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.staff_user_id == staff.id,
                OrganizationMembership.status == Status.ACTIVE,
            )
        )
        if membership is None:
            await self.audit.record(
                organization.id,
                "staff",
                "auth.login",
                "denied",
                actor_id=staff.id,
                reason_code="INACTIVE_OR_MISSING_MEMBERSHIP",
                metadata={"method": "password"},
            )
            await self.session.commit()
            raise AuthenticationError
        token = build_cookie_token(organization.id)
        self.session.add(
            StaffSession(
                organization_id=organization.id,
                staff_user_id=staff.id,
                token_hash=hash_session_token(token),
                expires_at=self._expiry(),
            )
        )
        await self.audit.record(
            organization.id,
            "staff",
            "auth.login",
            "success",
            actor_id=staff.id,
            metadata={"method": "password", "role": membership.role.value},
        )
        await self.session.commit()
        return token, staff, membership.role

    async def authenticate(self, token: str) -> Principal:
        organization_id = organization_from_cookie(token)
        await set_tenant_scope(self.session, organization_id)
        token_hash = hash_session_token(token)
        now = datetime.now(timezone.utc)
        customer_session = await self.session.scalar(
            select(CustomerSession).where(
                CustomerSession.organization_id == organization_id,
                CustomerSession.token_hash == token_hash,
                CustomerSession.revoked_at.is_(None),
                CustomerSession.expires_at > now,
            )
        )
        if customer_session:
            return Principal(
                "customer", organization_id, customer_session.customer_id, None, customer_session.id
            )
        staff_session = await self.session.scalar(
            select(StaffSession).where(
                StaffSession.organization_id == organization_id,
                StaffSession.token_hash == token_hash,
                StaffSession.revoked_at.is_(None),
                StaffSession.expires_at > now,
            )
        )
        if staff_session:
            membership = await self.session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.staff_user_id == staff_session.staff_user_id,
                    OrganizationMembership.status == Status.ACTIVE,
                )
            )
            if membership:
                return Principal(
                    "staff",
                    organization_id,
                    staff_session.staff_user_id,
                    membership.role,
                    staff_session.id,
                )
        raise AuthenticationError

    async def logout(self, principal: Principal) -> None:
        model = CustomerSession if principal.kind == "customer" else StaffSession
        await self.session.execute(
            update(model)
            .where(model.id == principal.session_id)
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await self.audit.record(
            principal.organization_id,
            principal.kind,
            "auth.logout",
            "success",
            actor_id=principal.subject_id,
        )
        await self.session.commit()


def require_admin(principal: Principal) -> None:
    if principal.kind != "staff" or principal.role != Role.ADMIN:
        raise AuthorizationError
