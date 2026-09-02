import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_mock_settings_do_not_require_external_credentials() -> None:
    settings = Settings(app_env="test")
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
    settings = Settings(app_env="test", postgres_password=secret, openai_api_key=secret)
    assert secret not in repr(settings)


def test_enabled_integration_modes_accept_complete_credentials() -> None:
    settings = Settings(
        app_env="test",
        commerce_provider="shopify",
        shopify_store_domain="synthetic.myshopify.com",
        shopify_access_token="synthetic-token",
        crm_provider="hubspot",
        hubspot_access_token="synthetic-token",
    )
    assert settings.commerce_provider == "shopify"
    assert settings.crm_provider == "hubspot"


def test_exact_provider_environment_names_are_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMERCE_PROVIDER", "mock")
    monkeypatch.setenv("CRM_PROVIDER", "mock")
    settings = Settings(app_env="test")
    assert (settings.commerce_provider, settings.crm_provider) == ("mock", "mock")


def test_database_url_encodes_password_characters() -> None:
    settings = Settings(app_env="test", postgres_password="synthetic:p@ss/word")
    assert "synthetic%3Ap%40ss%2Fword" in settings.database_url
