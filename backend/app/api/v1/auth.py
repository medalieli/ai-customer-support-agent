from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import CurrentPrincipal, DatabaseSession, RequestSettings
from app.core.config import Settings
from app.domain.models import Customer, Organization, Status
from app.services.auth import AuthenticationError, AuthService, require_admin

router = APIRouter(prefix="/auth", tags=["identity"])


class DemoLoginRequest(BaseModel):
    organization_slug: str = Field(min_length=1, max_length=80)
    persona_key: str = Field(min_length=1, max_length=80)


class StaffLoginRequest(BaseModel):
    organization_slug: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class IdentityResponse(BaseModel):
    kind: Literal["customer", "staff"]
    organization_id: str
    subject_id: str
    role: str | None


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


@router.get("/demo-personas")
async def list_demo_personas(
    session: DatabaseSession, settings: RequestSettings
) -> list[dict[str, str]]:
    if not settings.demo_auth_enabled:
        raise AuthenticationError
    organizations = list(await session.scalars(select(Organization).order_by(Organization.slug)))
    personas: list[dict[str, str]] = []
    from app.infrastructure.database import set_tenant_scope

    for organization in organizations:
        await set_tenant_scope(session, organization.id)
        customers = await session.scalars(
            select(Customer)
            .where(Customer.demo_key.is_not(None), Customer.status == Status.ACTIVE)
            .order_by(Customer.demo_key)
        )
        personas.extend(
            {
                "persona_key": customer.demo_key or "",
                "display_name": customer.display_name,
                "locale": customer.locale,
                "organization_slug": organization.slug,
            }
            for customer in customers
        )
    return personas


@router.post("/demo-login", response_model=IdentityResponse)
async def demo_login(
    payload: DemoLoginRequest,
    response: Response,
    session: DatabaseSession,
    settings: RequestSettings,
) -> IdentityResponse:
    token, customer = await AuthService(session, settings).demo_customer_login(
        payload.organization_slug, payload.persona_key
    )
    set_session_cookie(response, token, settings)
    return IdentityResponse(
        kind="customer",
        organization_id=str(customer.organization_id),
        subject_id=str(customer.id),
        role=None,
    )


@router.post("/staff-login", response_model=IdentityResponse)
async def staff_login(
    payload: StaffLoginRequest,
    response: Response,
    session: DatabaseSession,
    settings: RequestSettings,
) -> IdentityResponse:
    token, staff, role = await AuthService(session, settings).staff_login(
        payload.organization_slug, payload.email.lower(), payload.password
    )
    set_session_cookie(response, token, settings)
    return IdentityResponse(
        kind="staff",
        organization_id=str(token.split(".", 1)[0]),
        subject_id=str(staff.id),
        role=role.value,
    )


@router.get("/me", response_model=IdentityResponse)
async def current_identity(principal: CurrentPrincipal) -> IdentityResponse:
    return IdentityResponse(
        kind=principal.kind,
        organization_id=str(principal.organization_id),
        subject_id=str(principal.subject_id),
        role=principal.role.value if principal.role else None,
    )


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    request: Request,
    principal: CurrentPrincipal,
    session: DatabaseSession,
) -> None:
    settings: Settings = request.app.state.settings
    await AuthService(session, settings).logout(principal)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/admin-check")
async def admin_check(principal: CurrentPrincipal) -> dict[str, str]:
    require_admin(principal)
    return {"status": "admin_authorized"}
