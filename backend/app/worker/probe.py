import asyncio

from arq import create_pool

from app.worker.main import WorkerSettings


async def probe() -> None:
    redis = await create_pool(WorkerSettings.redis_settings)
    try:
        job = await redis.enqueue_job("health_verification", "worker-probe")
        if job is None:
            raise RuntimeError("worker health task could not be enqueued")
        result = await job.result(timeout=10)
        if result != {"status": "ok", "probe": "worker-probe"}:
            raise RuntimeError("worker health task returned an invalid result")
        print("worker health task completed")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(probe())
