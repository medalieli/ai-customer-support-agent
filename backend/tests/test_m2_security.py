from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.domain.models import Role
from app.services.auth import (
    AuthenticationError,
    AuthorizationError,
    Principal,
    build_cookie_token,
    organization_from_cookie,
    require_admin,
)
from app.services.security import (
    hash_password,
    hash_session_token,
    new_session_secret,
    verify_password,
)


def test_password_and_session_secrets_are_one_way() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)
    assert password not in password_hash
    assert verify_password(password_hash, password)
    assert not verify_password(password_hash, "wrong password")
    assert not verify_password("not-an-argon-hash", password)

    secret = new_session_secret()
    digest = hash_session_token(secret)
    assert secret.encode() not in digest
    assert len(digest) == 32
    assert hash_session_token(secret) == digest


def test_cookie_token_contains_only_routing_organization_and_random_secret() -> None:
    organization_id = UUID("10000000-0000-0000-0000-000000000001")
    token = build_cookie_token(organization_id)
    assert organization_from_cookie(token) == organization_id
    assert len(token.split(".", 1)[1]) >= 40
    with pytest.raises(AuthenticationError):
        organization_from_cookie("malformed")


def test_admin_permission_is_explicit() -> None:
    organization_id = UUID("10000000-0000-0000-0000-000000000001")
    subject_id = UUID("10000000-0000-0000-0000-000000000101")
    session_id = UUID("10000000-0000-0000-0000-000000000301")
    require_admin(Principal("staff", organization_id, subject_id, Role.ADMIN, session_id))
    with pytest.raises(AuthorizationError):
        require_admin(Principal("staff", organization_id, subject_id, Role.SUPPORT, session_id))
    with pytest.raises(AuthorizationError):
        require_admin(Principal("customer", organization_id, subject_id, None, session_id))


def test_production_rejects_demo_and_insecure_cookie_configuration() -> None:
    with pytest.raises(ValidationError, match="Demo authentication"):
        Settings(
            app_env="production",
            demo_auth_enabled=True,
            demo_staff_password=SecretStr("synthetic-only-password"),
            session_cookie_secure=True,
        )
    with pytest.raises(ValidationError, match="cookies must be secure"):
        Settings(app_env="production", demo_auth_enabled=False, session_cookie_secure=False)
    with pytest.raises(ValidationError, match="requires a demo staff password"):
        Settings(app_env="test", demo_auth_enabled=True, demo_staff_password=None)
