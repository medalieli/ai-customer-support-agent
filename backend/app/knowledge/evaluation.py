import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import TypedDict
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.infrastructure.database import create_database_engine
from app.knowledge.retrieval import retrieve_passages

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "retrieval_cases.json"
TENANTS = {
    "novacart": UUID("10000000-0000-0000-0000-000000000001"),
    "orbit-outlet": UUID("20000000-0000-0000-0000-000000000001"),
}


class Case(TypedDict):
    id: str
    tenant: str
    query: str
    expected: str | None
    language: str


async def evaluate() -> dict[str, float | int]:
    settings = get_settings()
    cases: list[Case] = json.loads(DATASET.read_text(encoding="utf-8"))
    engine = create_database_engine(settings)
    reciprocal_ranks: list[float] = []
    recalled = 0
    isolation_failures = 0
    latencies_ms: list[float] = []
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            for case in cases:
                started = perf_counter()
                results = await retrieve_passages(
                    session,
                    settings,
                    TENANTS[case["tenant"]],
                    case["query"],
                    language=case.get("language"),
                    top_k=5,
                )
                latencies_ms.append((perf_counter() - started) * 1000)
                expected = case["expected"]
                if expected is None:
                    if results:
                        isolation_failures += 1
                    continue
                expected_id = uuid5(NAMESPACE_URL, f"{case['tenant']}:{expected}")
                rank = next(
                    (
                        index
                        for index, result in enumerate(results, start=1)
                        if result.citation.document_id == expected_id
                    ),
                    0,
                )
                if rank:
                    recalled += 1
                    reciprocal_ranks.append(1 / rank)
                else:
                    reciprocal_ranks.append(0.0)
    finally:
        await engine.dispose()
    relevant = len(reciprocal_ranks)
    ordered_latencies = sorted(latencies_ms)
    p95_index = max(0, int(len(ordered_latencies) * 0.95 + 0.999) - 1)
    return {
        "cases": len(cases),
        "relevant_cases": relevant,
        "recall_at_5": recalled / relevant if relevant else 0.0,
        "mrr": sum(reciprocal_ranks) / relevant if relevant else 0.0,
        "unsupported_or_isolation_false_positives": isolation_failures,
        "mean_latency_ms": sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0,
        "p95_latency_ms": ordered_latencies[p95_index] if ordered_latencies else 0.0,
    }


async def main() -> None:
    print(json.dumps(await evaluate(), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
