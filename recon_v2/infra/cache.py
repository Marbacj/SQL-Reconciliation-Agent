"""Cache 抽象：Redis 优先，自动降级到内存 LRU。

设计：
- `CacheBackend` 接口（get / set）
- `RedisCache` 远程实现
- `InMemoryCache` 基于 cachetools.TTLCache 的本地实现
- `build_cache()` 工厂：读 env，尝试 Redis，失败/不可达回退内存
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class CacheBackend(Protocol):
    name: str

    def get(self, key: str) -> Optional[str]:  # pragma: no cover
        ...

    def set(self, key: str, value: str, ttl_seconds: int = 3600) -> None:  # pragma: no cover
        ...


class InMemoryCache:
    name = "memory"

    def __init__(self, maxsize: int = 4096, ttl: int = 3600):
        try:
            from cachetools import TTLCache  # type: ignore

            self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        except ImportError:
            # 极端降级：纯 dict（无 TTL）
            self._cache = {}

    def get(self, key: str) -> Optional[str]:
        try:
            return self._cache.get(key)
        except KeyError:
            return None

    def set(self, key: str, value: str, ttl_seconds: int = 3600) -> None:
        try:
            self._cache[key] = value
        except Exception:
            pass


class RedisCache:
    name = "redis"

    def __init__(self, url: str):
        # 延迟 import，避免 redis 未安装时整个模块挂掉
        import redis  # type: ignore

        self._client = redis.from_url(url, decode_responses=True, socket_timeout=2)
        # ping 一下确认可用
        self._client.ping()

    def get(self, key: str) -> Optional[str]:
        try:
            return self._client.get(key)
        except Exception as e:
            logger.warning("RedisCache.get failed: %s", e)
            return None

    def set(self, key: str, value: str, ttl_seconds: int = 3600) -> None:
        try:
            self._client.setex(key, ttl_seconds, value)
        except Exception as e:
            logger.warning("RedisCache.set failed: %s", e)


def build_cache(redis_url: Optional[str] = None) -> CacheBackend:
    """工厂：根据环境变量构造 Cache，失败自动降级。"""
    url = redis_url or os.getenv("REDIS_URL")
    if url:
        try:
            cache = RedisCache(url)
            logger.info("LLM cache backend = redis")
            return cache
        except Exception as e:
            logger.warning("Redis init failed (%s), fallback to memory cache", e)

    logger.info("LLM cache backend = memory")
    return InMemoryCache(
        ttl=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
    )
