import asyncio
import threading
from concurrent.futures import Future
from typing import Any

import redis.asyncio as aioredis

from log_conf.logger_setup import get_logger
logger =get_logger()



class RedisClient:
    _instance = None
    _pool = None
    # _thread_lock = threading.Lock()

    def __new__(cls):

        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pool = None
            cls._instance._lock = None
        return cls._instance

    async def initialize(self, host: str = "redis", port: int = 6379):
        """Create the connection pool once."""

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._pool is None:
                self._pool = aioredis.BlockingConnectionPool(
                    host=host,
                    port=port,
                    timeout=15,
                    decode_responses=True,
                    max_connections=50,
                )
                logger.info("[REDIS] Connection pool initialized")

    @property
    def client(self) -> aioredis.Redis:
        """Return a client using the shared pool — no new connection created."""
        if self._pool is None:
            raise RuntimeError("RedisClient not initialized. Call initialize() first.")
        return aioredis.Redis(connection_pool=self._pool)

    async def cleanup(self):
        """Close the pool on shutdown."""
        if self._lock is None:
            return
        async with self._lock:
            if self._pool:
                await self._pool.disconnect()
                logger.info("[REDIS] Connection pool closed")
                self._pool = None


# Singleton instance
redis_client = RedisClient()
