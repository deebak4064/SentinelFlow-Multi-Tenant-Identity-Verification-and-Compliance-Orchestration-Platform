"""Orchestration worker: `python -m app.worker`. Scale horizontally by running more replicas.

Claims are atomic (UPDATE ... WHERE status='queued'), so duplicate deliveries are harmless.
A reaper re-queues requests stuck in-flight (e.g. a worker or API process died mid-run).
"""

import asyncio
import logging
import signal
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.engine.orchestrator import claim, run_workflow
from app.models import VerificationRequest
from app.services.cache import QUEUE_KEY, enqueue_workflow, redis

log = logging.getLogger("sentinelflow.worker")
CONCURRENCY = 16
STALE_AFTER = timedelta(minutes=5)


async def reap_stale() -> int:
    async with SessionLocal() as db:
        ids = (await db.scalars(
            update(VerificationRequest)
            .where(VerificationRequest.status.in_(["queued", "processing"]), VerificationRequest.updated_at < datetime.now(UTC) - STALE_AFTER)
            .values(status="queued").returning(VerificationRequest.id))).all()
        await db.commit()
    for i in ids:
        await enqueue_workflow(str(i))
    return len(ids)


async def handle(vid: str) -> None:
    try:
        if await claim(uuid.UUID(vid)):
            await run_workflow(uuid.UUID(vid), "queue")
    except Exception:
        log.exception("job %s crashed", vid)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks: set[asyncio.Task] = set()
    last_reap = datetime.min.replace(tzinfo=UTC)
    log.info("worker started (concurrency=%d)", CONCURRENCY)
    while not stop.is_set():
        if datetime.now(UTC) - last_reap > timedelta(seconds=60):
            if n := await reap_stale():
                log.warning("re-queued %d stale requests", n)
            last_reap = datetime.now(UTC)
        item = await redis.brpop(QUEUE_KEY, timeout=1)
        if not item:
            continue
        await sem.acquire()
        t = asyncio.create_task(handle(item[1]))
        tasks.add(t)
        t.add_done_callback(lambda t: (tasks.discard(t), sem.release()))
    log.info("draining %d jobs", len(tasks))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
