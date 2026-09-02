from arq.connections import ArqRedis, RedisSettings, create_pool
from redis.asyncio import Redis

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    password = settings.redis_password.get_secret_value() if settings.redis_password else None
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=password,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_connect_timeout_seconds,
        decode_responses=True,
    )


async def close_redis_client(client: Redis) -> None:
    await client.aclose()


async def create_job_queue(settings: Settings) -> ArqRedis:
    return await create_pool(
        RedisSettings(
            host=settings.redis_host,
            port=settings.redis_port,
            database=settings.redis_db,
            password=(
                settings.redis_password.get_secret_value() if settings.redis_password else None
            ),
            conn_timeout=max(1, int(settings.redis_connect_timeout_seconds)),
        )
    )
