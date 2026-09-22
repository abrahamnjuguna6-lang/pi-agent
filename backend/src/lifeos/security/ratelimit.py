"""Sliding-window counters in Redis sorted sets (design §21.5, §33.4).

Scores are timestamps from the injected Clock (not Redis TIME), so windows are testable.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from redis.asyncio import Redis

from lifeos.domain.clock import Clock


class SlidingWindow:
    def __init__(self, redis: Redis, clock: Clock, prefix: str, window: timedelta) -> None:
        self._redis = redis
        self._clock = clock
        self._prefix = prefix
        self._window = window

    def _key(self, subject: str) -> str:
        return f"{self._prefix}:{subject}"

    async def hit(self, subject: str) -> int:
        """Record one event and return the number of events inside the window (including this one)."""
        now = self._clock.now().timestamp()
        key = self._key(subject)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - self._window.total_seconds())
            pipe.zadd(key, {f"{now}:{uuid.uuid4().hex}": now})
            pipe.zcard(key)
            pipe.expire(key, int(self._window.total_seconds()) + 60)
            results = await pipe.execute()
        return int(results[2])

    async def count(self, subject: str) -> int:
        now = self._clock.now().timestamp()
        return int(await self._redis.zcount(self._key(subject), now - self._window.total_seconds(), "+inf"))

    async def reset(self, subject: str) -> None:
        await self._redis.delete(self._key(subject))
