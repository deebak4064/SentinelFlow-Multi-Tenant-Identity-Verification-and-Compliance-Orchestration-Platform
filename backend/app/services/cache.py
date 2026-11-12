"""Redis access: rate limiting, response caching, hit/miss counters, workflow queue."""

import json
import logging
import time

import redis.asyncio as aioredis

from app.config import get_settings

log = logging.getLogger("sentinelflow.cache")
redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)

QUEUE_KEY = "sf:workflow:queue"


async def rate_limit(bucket: str, limit: int, window_s: int = 60) -> tuple[bool, int, int]:
    """Fixed-window counter. Returns (allowed, remaining, reset_seconds).

    ponytail: fixed window allows up to 2x burst at window edges; switch to a sliding-log
    Lua script if that matters.
    """
    window = int(time.time() // window_s)
    key = f"sf:rl:{bucket}:{window}"
    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_s + 1)
        count, _ = await pipe.execute()
    except aioredis.RedisError:
        # Fail open for availability, but make it loud. Login brute force is still capped by the
        # per-account lockout stored in Postgres.
        log.warning("rate limiter unavailable; failing open")
        return True, limit, window_s
    reset = window_s - int(time.time() % window_s)
    return count <= limit, max(limit - count, 0), reset


async def cache_get_json(key: str, metric: str):
    try:
        raw = await redis.get(key)
        await redis.hincrby("sf:cache:stats", f"{metric}:{'hit' if raw else 'miss'}", 1)
        return json.loads(raw) if raw else None
    except aioredis.RedisError:
        return None


async def cache_set_json(key: str, value, ttl_s: int) -> None:
    try:
        await redis.set(key, json.dumps(value, default=str), ex=ttl_s)
    except aioredis.RedisError:
        pass


async def cache_stats() -> dict:
    try:
        raw = await redis.hgetall("sf:cache:stats")
    except aioredis.RedisError:
        return {}
    out: dict[str, dict] = {}
    for k, v in raw.items():
        metric, kind = k.rsplit(":", 1)
        out.setdefault(metric, {"hit": 0, "miss": 0})[kind] = int(v)
    for m in out.values():
        total = m["hit"] + m["miss"]
        m["hit_rate"] = round(m["hit"] / total, 4) if total else None
    return out


async def enqueue_workflow(verification_id: str) -> None:
    await redis.lpush(QUEUE_KEY, verification_id)
