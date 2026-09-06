"""Prepare LangGraph-owned schema objects under the migration identity."""

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import get_settings


async def prepare() -> None:
    settings = get_settings()
    url = settings.database_url.replace("postgresql+asyncpg", "postgresql")
    context = AsyncPostgresSaver.from_conn_string(url)
    saver = await context.__aenter__()
    try:
        await saver.setup()
    finally:
        await context.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(prepare())
