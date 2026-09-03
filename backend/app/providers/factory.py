import httpx

from app.core.config import Settings
from app.providers.http import ProviderHttpClient
from app.providers.hubspot import HubSpotAdapter
from app.providers.mock_commerce import MockCommerceAdapter
from app.providers.mock_crm import MockCrmAdapter
from app.providers.ports import CommerceProviderV1, CrmProviderV1
from app.providers.shopify import ShopifyAdapter


def create_commerce_provider(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> CommerceProviderV1:
    if settings.commerce_provider == "mock":
        http = ProviderHttpClient(
            base_url=str(settings.mock_commerce_url),
            timeout=settings.provider_read_timeout_seconds,
            retries=settings.provider_max_retries,
            headers={
                "X-Internal-API-Key": settings.mock_commerce_internal_api_key.get_secret_value()
            },
            client=client,
        )
        return MockCommerceAdapter(http)
    http = ProviderHttpClient(
        base_url=f"https://{settings.shopify_store_domain}/admin/api/{settings.shopify_api_version}/graphql.json",
        timeout=settings.provider_write_timeout_seconds,
        retries=settings.provider_max_retries,
        headers={
            "X-Shopify-Access-Token": settings.shopify_access_token.get_secret_value()
            if settings.shopify_access_token
            else ""
        },
        client=client,
    )
    return ShopifyAdapter(http)


def create_crm_provider(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> CrmProviderV1:
    if settings.crm_provider == "mock":
        http = ProviderHttpClient(
            base_url=str(settings.mock_crm_url),
            timeout=settings.provider_read_timeout_seconds,
            retries=settings.provider_max_retries,
            headers={"X-Internal-API-Key": settings.mock_crm_internal_api_key.get_secret_value()},
            client=client,
        )
        return MockCrmAdapter(http)
    token = (
        settings.hubspot_access_token.get_secret_value() if settings.hubspot_access_token else ""
    )
    http = ProviderHttpClient(
        base_url="https://api.hubapi.com",
        timeout=settings.provider_write_timeout_seconds,
        retries=settings.provider_max_retries,
        headers={"Authorization": f"Bearer {token}"},
        client=client,
    )
    return HubSpotAdapter(http)
