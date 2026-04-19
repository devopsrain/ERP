"""
Redis Caching Layer

Provides a simple, dependency-injected Redis cache.

Usage:
    from fastapi import Depends
    from .cache import get_redis_cache, RedisCache

    @router.get("/some-data")
    async def get_some_data(cache: RedisCache = Depends(get_redis_cache)):
        cached_data = await cache.get("my-key")
        if cached_data:
            return cached_data
        
        db_data = await get_data_from_db()
        await cache.set("my-key", db_data, expire=3600) # Cache for 1 hour
        return db_data
"""
import logging
import os
from functools import lru_cache
from typing import Optional

import redis.asyncio as redis
from fastapi import Request

logger = logging.getLogger(__name__)

class RedisCache:
    """A wrapper around a redis.asyncio.Redis connection pool."""

    def __init__(self, pool: redis.Redis):
        self._pool = pool

    async def get(self, key: str) -> Optional[str]:
        try:
            return await self._pool.get(key)
        except Exception as e:
            logger.error("Redis GET failed for key '%s': %s", key, e)
            return None

    async def set(self, key: str, value: str, expire: int | None = None):
        """Set a key-value pair with an optional TTL in seconds."""
        try:
            await self._pool.set(key, value, ex=expire)
        except Exception as e:
            logger.error("Redis SET failed for key '%s': %s", key, e)

    async def clear(self, key: str) -> int:
        """Delete a key."""
        try:
            return await self._pool.delete(key)
        except Exception as e:
            logger.error("Redis DELETE failed for key '%s': %s", key, e)
            return 0

@lru_cache(maxsize=1)
def get_redis_pool() -> redis.Redis:
    """
    Creates and returns a Redis connection pool.
    Uses LRU cache to ensure only one pool is created.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        logger.warning("REDIS_URL not set. Redis cache is unavailable.")
        # Return a dummy object that does nothing if Redis is not configured
        class DummyRedis:
            async def get(self, *args, **kwargs): return None
            async def set(self, *args, **kwargs): pass
            async def delete(self, *args, **kwargs): return 0
        
        return DummyRedis()

    logger.info("Creating Redis connection pool for URL: %s", redis_url)
    return redis.from_url(redis_url, encoding="utf-8", decode_responses=True)

async def get_redis_cache(request: Request) -> RedisCache:
    """FastAPI dependency to get a RedisCache instance."""
    pool = request.app.state.redis_pool
    return RedisCache(pool)

async def close_redis_pool():
    """Gracefully close the Redis connection pool."""
    pool = get_redis_pool()
    if hasattr(pool, 'close'):
        logger.info("Closing Redis connection pool.")
        await pool.close()
