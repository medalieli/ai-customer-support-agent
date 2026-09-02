from typing import Annotated

from fastapi import Cookie, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.database import get_db_session
from app.services.auth import AuthenticationError, AuthService, Principal

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_request_settings(request: Request) -> Settings:
    return request.app.state.settings


RequestSettings = Annotated[Settings, Depends(get_request_settings)]


async def get_current_principal(
    request: Request,
    session: DatabaseSession,
    session_cookie: Annotated[str | None, Cookie(alias="novacart_session")] = None,
) -> Principal:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name) or session_cookie
    if not token:
        raise AuthenticationError
    return await AuthService(session, settings).authenticate(token)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
