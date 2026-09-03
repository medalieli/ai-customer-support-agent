from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, RateLimitError
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.knowledge.embeddings import (
    DeterministicFakeEmbeddings,
    EmbeddingProviderError,
    OpenAIEmbeddings,
    create_embedding_provider,
    indexing_fingerprint,
)
from app.knowledge.rerankers import (
    CrossEncoderReranker,
    DeterministicReranker,
    RerankerError,
    create_reranker,
)


class FakeEmbeddingsEndpoint:
    def __init__(self, *, dimensions: int = 1536, error: Exception | None = None) -> None:
        self.dimensions = dimensions
        self.error = error
        self.calls: list[list[str]] = []

    async def create(self, *, model: str, input: list[str], dimensions: int) -> Any:
        self.calls.append(input)
        if self.error:
            raise self.error
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index + 1)] * self.dimensions)
                for index, _ in enumerate(input)
            ]
        )


def openai_provider(endpoint: FakeEmbeddingsEndpoint) -> OpenAIEmbeddings:
    client = cast(Any, SimpleNamespace(embeddings=endpoint))
    return OpenAIEmbeddings(
        api_key="synthetic-not-real",
        model="text-embedding-3-small",
        dimensions=1536,
        batch_size=2,
        timeout_seconds=1,
        max_retries=0,
        client=client,
    )


@pytest.mark.asyncio
async def test_openai_embedding_batches_and_validates_dimensions() -> None:
    endpoint = FakeEmbeddingsEndpoint()
    vectors = await openai_provider(endpoint).embed(["one", "two", "three", "four", "five"])
    assert [len(call) for call in endpoint.calls] == [2, 2, 1]
    assert len(vectors) == 5 and all(len(vector) == 1536 for vector in vectors)

    with pytest.raises(EmbeddingProviderError, match="embedding_dimension_mismatch"):
        await openai_provider(FakeEmbeddingsEndpoint(dimensions=8)).embed(["bad"])


@pytest.mark.asyncio
async def test_openai_embedding_closes_internally_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, **_: Any) -> None:
            self.embeddings = FakeEmbeddingsEndpoint()
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    client = FakeClient()
    monkeypatch.setattr("app.knowledge.embeddings.AsyncOpenAI", lambda **_: client)
    provider = OpenAIEmbeddings(
        api_key="synthetic-not-real",
        model="text-embedding-3-small",
        dimensions=1536,
        batch_size=2,
        timeout_seconds=1,
        max_retries=0,
    )

    await provider.embed(["safe input"])

    assert client.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
            "embedding_unavailable",
        ),
        (
            APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
            "embedding_timeout",
        ),
        (
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://api.openai.com")
                ),
                body=None,
            ),
            "embedding_rate_limited",
        ),
    ],
)
async def test_openai_embedding_errors_are_safe(error: Exception, code: str) -> None:
    with pytest.raises(EmbeddingProviderError, match=code) as captured:
        await openai_provider(FakeEmbeddingsEndpoint(error=error)).embed(["safe input"])
    assert captured.value.retryable


def test_embedding_selection_fingerprint_and_production_guards() -> None:
    test_settings = Settings(app_env="test", embedding_provider="fake")
    fake = create_embedding_provider(test_settings)
    assert isinstance(fake, DeterministicFakeEmbeddings) and fake.dimensions == 1536
    assert indexing_fingerprint(test_settings, fake) == indexing_fingerprint(test_settings, fake)
    changed = Settings(app_env="test", embedding_provider="fake", chunk_size_words=121)
    assert indexing_fingerprint(test_settings, fake) != indexing_fingerprint(changed, fake)

    openai_settings = Settings(
        app_env="test",
        embedding_provider="openai",
        openai_api_key=SecretStr("synthetic-not-real"),
    )
    assert isinstance(create_embedding_provider(openai_settings), OpenAIEmbeddings)
    openai_settings.openai_api_key = None
    with pytest.raises(EmbeddingProviderError, match="embedding_credentials_missing"):
        create_embedding_provider(openai_settings)
    with pytest.raises(ValidationError, match="OpenAI embedding mode requires"):
        Settings(app_env="test", embedding_provider="openai", openai_api_key=None)
    with pytest.raises(ValidationError, match="OpenAI embedding mode requires"):
        Settings(app_env="test", embedding_provider="openai", openai_api_key=SecretStr(""))
    with pytest.raises(ValidationError, match="Fake embeddings cannot"):
        Settings(
            app_env="production",
            demo_auth_enabled=False,
            session_cookie_secure=True,
            embedding_provider="fake",
            reranker_provider="cross_encoder",
        )
    with pytest.raises(ValidationError, match="Deterministic reranking cannot"):
        Settings(
            app_env="production",
            demo_auth_enabled=False,
            session_cookie_secure=True,
            embedding_provider="openai",
            openai_api_key=SecretStr("synthetic-not-real"),
            reranker_provider="deterministic",
        )


@pytest.mark.asyncio
async def test_reranker_selection_cache_and_no_silent_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deterministic = create_reranker(Settings(app_env="test", reranker_provider="deterministic"))
    assert isinstance(deterministic, DeterministicReranker)
    assert (await deterministic.score("garantie", ["warranty garantie"]))[0] > 0

    real = create_reranker(Settings(app_env="test", reranker_provider="cross_encoder"))
    assert isinstance(real, CrossEncoderReranker)
    assert await real.score("query", []) == []

    class FakeModel:
        def predict(self, pairs: list[tuple[str, str]], *, show_progress_bar: bool) -> list[float]:
            assert not show_progress_bar
            return [float(index) for index, _ in enumerate(pairs)]

    key = (real.model_name, real.cache_dir)
    monkeypatch.setattr(CrossEncoderReranker, "_models", {key: FakeModel()})
    assert await real.score("query", ["one", "two"]) == [0.0, 1.0]

    def unavailable(self: CrossEncoderReranker) -> Any:
        raise RerankerError("reranker_unavailable")

    monkeypatch.setattr(CrossEncoderReranker, "_model", unavailable)
    with pytest.raises(RerankerError, match="reranker_unavailable"):
        await real.score("query", ["passage"])
