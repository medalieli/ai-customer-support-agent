import asyncio
from pathlib import Path
from typing import Any, Protocol

from app.core.config import Settings


class RerankerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Reranker(Protocol):
    provider_name: str
    model_name: str

    async def score(self, query: str, passages: list[str]) -> list[float]: ...


class DeterministicReranker:
    provider_name = "deterministic"
    model_name = "multilingual-token-overlap-v2"

    async def score(self, query: str, passages: list[str]) -> list[float]:
        # Import here to keep token normalization in one place without an import cycle.
        from app.knowledge.retrieval import local_rerank

        return [local_rerank(query, passage) for passage in passages]


class CrossEncoderReranker:
    provider_name = "cross_encoder"
    _models: dict[tuple[str, str], Any] = {}

    def __init__(self, model: str, cache_dir: str) -> None:
        self.model_name = model
        self.cache_dir = str(Path(cache_dir).resolve())

    def _model(self) -> Any:
        key = (self.model_name, self.cache_dir)
        if key not in self._models:
            try:
                from sentence_transformers import CrossEncoder

                self._models[key] = CrossEncoder(self.model_name, cache_folder=self.cache_dir)
            except Exception as exc:
                raise RerankerError("reranker_unavailable") from exc
        return self._models[key]

    async def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        try:
            values = await asyncio.to_thread(
                self._model().predict,
                [(query, passage) for passage in passages],
                show_progress_bar=False,
            )
        except RerankerError:
            raise
        except Exception as exc:
            raise RerankerError("reranker_failed") from exc
        scores = [float(value) for value in values]
        if len(scores) != len(passages):
            raise RerankerError("reranker_invalid_response")
        return scores


def create_reranker(settings: Settings) -> Reranker:
    if settings.reranker_provider == "deterministic":
        return DeterministicReranker()
    return CrossEncoderReranker(settings.reranker_model, settings.reranker_model_cache)
