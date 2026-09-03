from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.knowledge.embeddings import EmbeddingProviderError
from app.knowledge.rerankers import RerankerError
from app.main import create_app


async def test_unhandled_error_response_hides_exception_details() -> None:
    app = create_app(Settings(app_env="test"))
    secret = "exception-secret-canary"

    @app.get("/_test/error")
    async def fail() -> None:
        raise RuntimeError(secret)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/_test/error")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    assert secret not in response.text
    assert "traceback" not in response.text.lower()


async def test_semantic_provider_errors_are_safe_and_explicit() -> None:
    app = create_app(Settings(app_env="test"))

    @app.get("/_test/embedding-error")
    async def embedding_failure() -> None:
        raise EmbeddingProviderError("embedding_rate_limited", retryable=True)

    @app.get("/_test/reranker-error")
    async def reranker_failure() -> None:
        raise RerankerError("reranker_unavailable")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        embedding = await client.get("/_test/embedding-error")
        reranker = await client.get("/_test/reranker-error")

    assert embedding.status_code == 503
    assert embedding.json()["error"]["code"] == "embedding_rate_limited"
    assert reranker.status_code == 503
    assert reranker.json()["error"]["code"] == "reranker_unavailable"
