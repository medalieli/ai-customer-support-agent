import hashlib
import json
import math
import re
from typing import Protocol

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.core.config import Settings


class EmbeddingProviderError(RuntimeError):
    """Safe provider failure whose code may be persisted or returned."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class DeterministicFakeEmbeddings:
    """Stable test-only feature hashing; never makes a network or paid API call."""

    provider_name = "fake"
    model_name = "deterministic-feature-hash-v2"

    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if any("[[EMBEDDING_FAILURE]]" in text for text in texts):
            raise EmbeddingProviderError("synthetic_embedding_failure", retryable=False)
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"\w+", text.casefold()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class OpenAIEmbeddings:
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        batch_size: int,
        timeout_seconds: float,
        max_retries: int,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model_name = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        client = self._client or AsyncOpenAI(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        try:
            for start in range(0, len(texts), self.batch_size):
                response = await client.embeddings.create(
                    model=self.model_name,
                    input=texts[start : start + self.batch_size],
                    dimensions=self.dimensions,
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                vectors.extend([list(item.embedding) for item in ordered])
        except RateLimitError as exc:
            raise EmbeddingProviderError("embedding_rate_limited", retryable=True) from exc
        except APITimeoutError as exc:
            raise EmbeddingProviderError("embedding_timeout", retryable=True) from exc
        except APIConnectionError as exc:
            raise EmbeddingProviderError("embedding_unavailable", retryable=True) from exc
        finally:
            if self._client is None:
                await client.close()
        if len(vectors) != len(texts) or any(len(vector) != self.dimensions for vector in vectors):
            raise EmbeddingProviderError("embedding_dimension_mismatch", retryable=False)
        return vectors


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        return DeterministicFakeEmbeddings(settings.embedding_dimensions)
    key = settings.openai_api_key
    if key is None or not key.get_secret_value().strip():
        raise EmbeddingProviderError("embedding_credentials_missing", retryable=False)
    return OpenAIEmbeddings(
        api_key=key.get_secret_value(),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        timeout_seconds=settings.embedding_timeout_seconds,
        max_retries=settings.embedding_max_retries,
    )


def indexing_fingerprint(settings: Settings, provider: EmbeddingProvider) -> str:
    payload = {
        "chunk_overlap_words": settings.chunk_overlap_words,
        "chunk_size_words": settings.chunk_size_words,
        "dimensions": provider.dimensions,
        "model": provider.model_name,
        "provider": provider.provider_name,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
