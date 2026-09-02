from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Validated process configuration loaded from NOVACART_* variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="NOVACART_",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "novacart"
    postgres_user: str = "novacart"
    postgres_password: SecretStr = SecretStr("")
    postgres_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    redis_host: str = "localhost"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr | None = None
    redis_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    demo_auth_enabled: bool = False
    demo_staff_password: SecretStr | None = None
    session_ttl_seconds: int = Field(default=3600, ge=300, le=2592000)
    session_cookie_name: str = "novacart_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict"] = "lax"

    commerce_provider: Literal["mock", "shopify"] = Field(
        default="mock",
        validation_alias=AliasChoices("COMMERCE_PROVIDER", "NOVACART_COMMERCE_PROVIDER"),
    )
    crm_provider: Literal["mock", "hubspot"] = Field(
        default="mock",
        validation_alias=AliasChoices("CRM_PROVIDER", "NOVACART_CRM_PROVIDER"),
    )
    openai_api_key: SecretStr | None = None
    shopify_store_domain: str | None = None
    shopify_access_token: SecretStr | None = None
    hubspot_access_token: SecretStr | None = None

    @computed_field(repr=False)  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @model_validator(mode="after")
    def validate_provider_credentials(self) -> "Settings":
        if self.app_env == "production" and self.demo_auth_enabled:
            raise ValueError("Demo authentication cannot be enabled in production")
        if self.demo_auth_enabled and not self.demo_staff_password:
            raise ValueError("Demo authentication requires a demo staff password")
        if self.app_env == "production" and not self.session_cookie_secure:
            raise ValueError("Production session cookies must be secure")
        if self.commerce_provider == "shopify" and not (
            self.shopify_store_domain and self.shopify_access_token
        ):
            raise ValueError("Shopify mode requires its store domain and access token")
        if self.crm_provider == "hubspot" and not self.hubspot_access_token:
            raise ValueError("HubSpot mode requires its access token")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
