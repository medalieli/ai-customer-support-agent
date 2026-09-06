from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOCK_CRM_", env_file=".env", extra="ignore")
    app_env: Literal["development", "test", "production"] = "development"
    database_path: str = "/data/crm.db"
    internal_api_key: SecretStr = SecretStr("")
    failure_simulation_enabled: bool = False
    simulated_timeout_seconds: float = Field(default=0.05, ge=0, le=5)
    webhook_target_url: AnyHttpUrl = AnyHttpUrl(
        "http://api:8000/api/v1/webhooks/mock_crm/novacart-crm"
    )
    webhook_secret: SecretStr = SecretStr("mock-crm-webhook-secret-at-least-32")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if len(self.internal_api_key.get_secret_value()) < 16:
            raise ValueError("Internal API key must contain at least 16 characters")
        if self.app_env == "production" and self.failure_simulation_enabled:
            raise ValueError("Failure simulation cannot be enabled in production")
        if self.app_env == "production" and self.webhook_secret.get_secret_value() == (
            "mock-crm-webhook-secret-at-least-32"
        ):
            raise ValueError("Production webhook secret must replace the development default")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
