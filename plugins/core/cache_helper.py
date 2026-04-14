"""
plugins/core/cache_helper.py — Кэш данных в памяти с TTL.

API:
    cache = CacheHelper(default_ttl=300)
    cache.set("key", value)
    cached = cache.get("key")             # None если нет/истёк
    cached = cache.get("key", default=42) # 42 если нет/истёк
    cache.delete("key")
    cache.cleanup()                       # удаляет просроченные
    stats = cache.stats()

Также есть декоратор @cached(ttl=60) для функций.
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger("plugins.cache")


class CacheHelper:
    """Простой кэш в памяти с TTL. Не плагин, а утилита."""

    def __init__(self, default_ttl: int = 300):
        self.default_ttl = default_ttl
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        item = self._store.get(key)
        if not item:
            return default
        expires_at, value = item
        if time.time() > expires_at:
            del self._store[key]
            return default
        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self.default_ttl
        self._store[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def cleanup(self) -> int:
        now = time.time()
        expired = [k for k, (exp, _) in self._store.items() if exp < now]
        for k in expired:
            del self._store[k]
        return len(expired)

    def clear(self) -> int:
        n = len(self._store)
        self._store.clear()
        return n

    def stats(self) -> dict:
        now = time.time()
        active = sum(1 for exp, _ in self._store.values() if exp > now)
        return {
            "total": len(self._store),
            "active": active,
            "expired": len(self._store) - active,
            "default_ttl": self.default_ttl,
        }


# Глобальный экземпляр для использования через декоратор
_global_cache = CacheHelper()


def cached(ttl: int = 300, key_prefix: str = ""):
    """
    Декоратор кэширования. Использует имя функции + аргументы как ключ.

    @cached(ttl=60)
    def expensive(x):
        return x * 2
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{key_prefix}:{func.__name__}:{args}:{sorted(kwargs.items())}"
            cached_val = _global_cache.get(key)
            if cached_val is not None:
                return cached_val
            result = func(*args, **kwargs)
            _global_cache.set(key, result, ttl=ttl)
            return result
        return wrapper
    return decorator


def get_global_cache() -> CacheHelper:
    return _global_cache
