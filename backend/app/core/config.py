from functools import lru_cache
from pathlib import Path
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
    service_name: str = "novacart-api"
    otel_enabled: bool = False
    otel_exporter_endpoint: AnyHttpUrl = AnyHttpUrl("http://otel-collector:4318")
    api_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    docs_enabled: bool = True
    metrics_enabled: bool = True
    trust_proxy_headers: bool = False

    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "novacart"
    postgres_user: str = "novacart"
    postgres_password: SecretStr = SecretStr("")
    postgres_password_file: Path | None = None
    postgres_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    redis_host: str = "localhost"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr | None = None
    redis_password_file: Path | None = None
    redis_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)

    demo_auth_enabled: bool = False
    demo_staff_password: SecretStr | None = None
    session_ttl_seconds: int = Field(default=3600, ge=300, le=2592000)
    session_cookie_name: str = "novacart_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict"] = "lax"
    csrf_protection_enabled: bool = True

    embedding_provider: Literal["fake", "openai"] = "fake"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1536, le=1536)
    embedding_batch_size: int = Field(default=64, ge=1, le=256)
    embedding_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    embedding_max_retries: int = Field(default=2, ge=0, le=5)
    reranker_provider: Literal["deterministic", "cross_encoder"] = "deterministic"
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_model_cache: str = ".cache/models"
    retrieval_lexical_k: int = Field(default=20, ge=1, le=100)
    retrieval_vector_k: int = Field(default=20, ge=1, le=100)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_min_reranker_score: float = -3.07
    chunk_size_words: int = Field(default=120, ge=20, le=500)
    chunk_overlap_words: int = Field(default=20, ge=0, le=100)

    commerce_provider: Literal["mock", "shopify"] = Field(
        default="mock",
        validation_alias=AliasChoices("COMMERCE_PROVIDER", "NOVACART_COMMERCE_PROVIDER"),
    )
    crm_provider: Literal["mock", "hubspot"] = Field(
        default="mock",
        validation_alias=AliasChoices("CRM_PROVIDER", "NOVACART_CRM_PROVIDER"),
    )
    provider_read_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    provider_write_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    provider_max_retries: int = Field(default=2, ge=0, le=3)
    mock_commerce_url: AnyHttpUrl = AnyHttpUrl("http://mock-commerce:8080")
    mock_commerce_internal_api_key: SecretStr = SecretStr("development-commerce-key")
    mock_crm_url: AnyHttpUrl = AnyHttpUrl("http://mock-crm:8090")
    mock_crm_internal_api_key: SecretStr = SecretStr("development-crm-key")
    shopify_api_version: str = "2026-01"
    openai_api_key: SecretStr | None = None
    agent_model: str = "gpt-5-mini"
    agent_provider: Literal["openai", "deterministic"] = "openai"
    agent_model_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    agent_max_steps: int = Field(default=8, ge=1, le=20)
    customer_rate_limit_per_minute: int = Field(default=30, ge=1, le=1000)
    actor_rate_limit_per_minute: int = Field(default=12, ge=1, le=1000)
    tenant_concurrent_runs: int = Field(default=8, ge=1, le=100)
    actor_concurrent_runs: int = Field(default=2, ge=1, le=20)
    tenant_daily_token_budget: int = Field(default=2_000_000, ge=1000)
    tenant_daily_cost_budget_usd: float = Field(default=25.0, ge=0.01)
    openai_input_cost_per_million_usd: float = Field(default=0.25, ge=0)
    openai_output_cost_per_million_usd: float = Field(default=2.0, ge=0)
    agent_min_confidence: float = Field(default=0.65, ge=0, le=1)
    action_secret: SecretStr = SecretStr("development-action-secret-change-me-32-bytes")
    action_confirmation_ttl_seconds: int = Field(default=600, ge=30, le=3600)
    shopify_store_domain: str | None = None
    shopify_access_token: SecretStr | None = None
    hubspot_access_token: SecretStr | None = None
    webhook_replay_window_seconds: int = Field(default=300, ge=30, le=3600)
    webhook_max_body_bytes: int = Field(default=262144, ge=1024, le=2097152)
    webhook_max_attempts: int = Field(default=5, ge=1, le=20)
    webhook_retry_base_seconds: int = Field(default=5, ge=1, le=300)
    mock_commerce_webhook_secret: SecretStr = SecretStr("mock-commerce-webhook-secret-32bytes")
    mock_crm_webhook_secret: SecretStr = SecretStr("mock-crm-webhook-secret-at-least-32")

    @staticmethod
    def _secret_from_file(path: Path, name: str) -> SecretStr:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Cannot read {name} secret file") from exc
        if not value:
            raise ValueError(f"{name} secret file is empty")
        return SecretStr(value)

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
        if self.postgres_password_file:
            self.postgres_password = self._secret_from_file(
                self.postgres_password_file, "PostgreSQL password"
            )
        if self.redis_password_file:
            self.redis_password = self._secret_from_file(self.redis_password_file, "Redis password")
        if self.chunk_overlap_words >= self.chunk_size_words:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.app_env == "production" and self.demo_auth_enabled:
            raise ValueError("Demo authentication cannot be enabled in production")
        if self.demo_auth_enabled and not self.demo_staff_password:
            raise ValueError("Demo authentication requires a demo staff password")
        if self.app_env == "production" and not self.session_cookie_secure:
            raise ValueError("Production session cookies must be secure")
        if self.app_env == "production":
            # Provider-safety errors stay first so operators see the most actionable cause.
            if self.embedding_provider == "fake":
                raise ValueError("Fake embeddings cannot be enabled in production")
            if self.reranker_provider == "deterministic":
                raise ValueError("Deterministic reranking cannot be enabled in production")
            weak = {
                "",
                "password",
                "changeme",
                "replace-with-a-strong-local-password",
                "development-action-secret-change-me-32-bytes",
            }
            postgres_secret = self.postgres_password.get_secret_value().lower()
            if (
                postgres_secret in weak
                or postgres_secret.startswith(("replace-", "read-from-", "inject-"))
                or len(postgres_secret) < 24
            ):
                raise ValueError("Production PostgreSQL password is missing or weak")
            action_secret = self.action_secret.get_secret_value().lower()
            if (
                action_secret in weak
                or action_secret.startswith(("replace-", "read-from-", "inject-"))
                or len(action_secret) < 32
            ):
                raise ValueError("Production action secret is missing or weak")
            if self.docs_enabled:
                raise ValueError("API documentation must be disabled in production")
            if not self.csrf_protection_enabled:
                raise ValueError("CSRF protection must be enabled in production")
            if self.log_level == "DEBUG":
                raise ValueError("Debug logging cannot be enabled in production")
            if str(self.api_url).lower().startswith("http://") or str(
                self.frontend_url
            ).lower().startswith("http://"):
                raise ValueError("Production public URLs must use HTTPS")
            if self.postgres_user.lower() in {"postgres", "root", "novacart"}:
                raise ValueError("Production must use a dedicated restricted runtime database role")
            if self.redis_password is None or len(self.redis_password.get_secret_value()) < 24:
                raise ValueError("Production Redis password is missing or weak")
            if any(
                value.get_secret_value().lower().startswith(("replace-", "read-from-", "inject-"))
                for value in (
                    self.mock_commerce_internal_api_key,
                    self.mock_crm_internal_api_key,
                    self.mock_commerce_webhook_secret,
                    self.mock_crm_webhook_secret,
                )
            ):
                raise ValueError("Production provider secrets must replace all placeholders")
            known_webhook_secrets = {
                "mock-commerce-webhook-secret-32bytes",
                "mock-crm-webhook-secret-at-least-32",
            }
            if (
                self.mock_commerce_webhook_secret.get_secret_value() in known_webhook_secrets
                or self.mock_crm_webhook_secret.get_secret_value() in known_webhook_secrets
            ):
                raise ValueError("Production webhook secrets must replace development defaults")
        if self.embedding_provider == "openai" and not (
            self.openai_api_key and self.openai_api_key.get_secret_value().strip()
        ):
            raise ValueError("OpenAI embedding mode requires an API key")
        if (
            self.app_env == "production"
            and self.openai_api_key
            and self.openai_api_key.get_secret_value().lower().startswith(("replace-", "inject-"))
        ):
            raise ValueError("Production OpenAI credential must replace its placeholder")
        if self.app_env == "production" and self.embedding_provider == "fake":
            raise ValueError("Fake embeddings cannot be enabled in production")
        if self.app_env == "production" and self.reranker_provider == "deterministic":
            raise ValueError("Deterministic reranking cannot be enabled in production")
        if self.agent_provider == "deterministic" and self.app_env != "test":
            raise ValueError("Deterministic agent models are allowed only in test")
        if self.commerce_provider == "shopify" and not (
            self.shopify_store_domain and self.shopify_access_token
        ):
            raise ValueError("Shopify mode requires its store domain and access token")
        if len(self.action_secret.get_secret_value().encode()) < 32:
            raise ValueError("Action secret must contain at least 32 bytes")
        if (
            self.commerce_provider == "mock"
            and len(self.mock_commerce_internal_api_key.get_secret_value()) < 16
        ):
            raise ValueError("Mock commerce mode requires its internal API key")
        if self.crm_provider == "hubspot" and not self.hubspot_access_token:
            raise ValueError("HubSpot mode requires its access token")
        if (
            self.crm_provider == "mock"
            and len(self.mock_crm_internal_api_key.get_secret_value()) < 16
        ):
            raise ValueError("Mock CRM mode requires its internal API key")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
