from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def test_mock_settings_do_not_require_external_credentials() -> None:
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        reranker_provider="deterministic",
        openai_api_key=None,
    )
    assert settings.commerce_provider == "mock"
    assert settings.crm_provider == "mock"
    assert settings.openai_api_key is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"commerce_provider": "shopify"}, "Shopify mode requires"),
        ({"crm_provider": "hubspot"}, "HubSpot mode requires"),
    ],
)
def test_integration_modes_require_credentials(overrides: dict[str, str], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        Settings(app_env="test", **overrides)  # type: ignore[arg-type]


def test_settings_repr_masks_secrets() -> None:
    secret = "settings-secret-canary"
    settings = Settings(
        app_env="test", postgres_password=SecretStr(secret), openai_api_key=SecretStr(secret)
    )
    assert secret not in repr(settings)


def test_enabled_integration_modes_accept_complete_credentials() -> None:
    settings = Settings(
        app_env="test",
        commerce_provider="shopify",
        shopify_store_domain="synthetic.myshopify.com",
        shopify_access_token=SecretStr("synthetic-token"),
        crm_provider="hubspot",
        hubspot_access_token=SecretStr("synthetic-token"),
    )
    assert settings.commerce_provider == "shopify"
    assert settings.crm_provider == "hubspot"


def test_exact_provider_environment_names_are_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMERCE_PROVIDER", "mock")
    monkeypatch.setenv("CRM_PROVIDER", "mock")
    settings = Settings(app_env="test")
    assert (settings.commerce_provider, settings.crm_provider) == ("mock", "mock")


def test_database_url_encodes_password_characters() -> None:
    settings = Settings(app_env="test", postgres_password=SecretStr("synthetic:p@ss/word"))
    assert "synthetic%3Ap%40ss%2Fword" in settings.database_url


def test_deterministic_agent_is_explicitly_test_only() -> None:
    settings = Settings(app_env="test", agent_provider="deterministic")
    assert settings.agent_provider == "deterministic"
    with pytest.raises(ValidationError, match="allowed only in test"):
        Settings(app_env="development", agent_provider="deterministic")


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "api_url": "https://support.example.test/api",
        "frontend_url": "https://support.example.test",
        "docs_enabled": False,
        "metrics_enabled": False,
        "demo_auth_enabled": False,
        "postgres_user": "novacart_runtime",
        "postgres_password": SecretStr("runtime-database-password-32-bytes"),
        "redis_password": SecretStr("redis-production-password-32-bytes"),
        "session_cookie_secure": True,
        "embedding_provider": "openai",
        "reranker_provider": "cross_encoder",
        "openai_api_key": SecretStr("synthetic-openai-key"),
        "action_secret": SecretStr("production-action-secret-with-32-bytes"),
        "mock_commerce_internal_api_key": SecretStr("production-commerce-key-32-bytes"),
        "mock_crm_internal_api_key": SecretStr("production-crm-key-32-bytes"),
        "mock_commerce_webhook_secret": SecretStr("unique-commerce-webhook-secret-32-bytes"),
        "mock_crm_webhook_secret": SecretStr("unique-crm-webhook-secret-more-than-32-bytes"),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"docs_enabled": True}, "documentation"),
        ({"csrf_protection_enabled": False}, "CSRF"),
        ({"log_level": "DEBUG"}, "Debug"),
        ({"api_url": "http://support.example.test"}, "HTTPS"),
        ({"postgres_user": "postgres"}, "restricted runtime"),
        ({"postgres_password": SecretStr("short")}, "missing or weak"),
        ({"redis_password": SecretStr("short")}, "Redis password"),
    ],
)
def test_production_rejects_insecure_settings(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        production_settings(**overrides)


def test_production_like_mock_providers_are_valid() -> None:
    settings = production_settings()
    assert settings.commerce_provider == "mock"
    assert settings.crm_provider == "mock"


def test_secret_files_are_loaded_and_fail_closed(tmp_path: Path) -> None:
    postgres = tmp_path / "postgres.txt"
    redis = tmp_path / "redis.txt"
    postgres.write_text("runtime-database-password-from-file", encoding="utf-8")
    redis.write_text("redis-production-password-from-file", encoding="utf-8")
    settings = production_settings(
        postgres_password_file=postgres,
        redis_password_file=redis,
    )
    assert settings.postgres_password.get_secret_value().endswith("from-file")
    assert settings.redis_password is not None
    assert settings.redis_password.get_secret_value().endswith("from-file")

    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError, match="secret file is empty"):
        production_settings(postgres_password_file=empty)
    with pytest.raises(ValidationError, match="Cannot read"):
        production_settings(postgres_password_file=tmp_path / "missing.txt")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"chunk_size_words": 20, "chunk_overlap_words": 20}, "Chunk overlap"),
        ({"action_secret": SecretStr("short")}, "Action secret"),
        ({"mock_commerce_internal_api_key": SecretStr("short")}, "Mock commerce"),
        ({"mock_crm_internal_api_key": SecretStr("short")}, "Mock CRM"),
    ],
)
def test_unsafe_configuration_is_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(app_env="test", **overrides)  # type: ignore[arg-type]
