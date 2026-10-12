import math
from datetime import datetime, timezone

from redis.asyncio import Redis

from contracts.cache import CacheEntry
from gateway.cache_rules import CacheContext


def expires_at(entry: CacheEntry) -> float:
    created = entry.created_at

    # Existing CacheEntry uses datetime.utcnow(), which is timezone-naive.
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    return created.timestamp() + entry.ttl_seconds


async def get_exact(
    redis_client: Redis,
    context: CacheContext,
) -> CacheEntry | None:
    raw = await redis_client.get(context.exact_key)

    if raw is None:
        return None

    entry = CacheEntry.model_validate_json(raw)

    if expires_at(entry) <= datetime.now(timezone.utc).timestamp():
        return None

    return entry


async def set_exact(
    redis_client: Redis,
    context: CacheContext,
    entry: CacheEntry,
) -> None:
    remaining = (
        expires_at(entry)
        - datetime.now(timezone.utc).timestamp()
    )

    if remaining <= 0:
        return

    await redis_client.set(
        context.exact_key,
        entry.model_dump_json(),
        ex=max(1, math.ceil(remaining)),
    )