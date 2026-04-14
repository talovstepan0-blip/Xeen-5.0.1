"""
core/llm_cache.py — SQLite-кэш LLM-ответов с TTL и офлайн-детектор.

API:
    cache = LLMCache(default_ttl=3600)
    hit = cache.get("prompt text")
    if hit is None:
        response = await call_llm(prompt)
        cache.set("prompt text", response, model="llama3.2", agent="fenix")

    detector = OfflineDetector()
    if detector.is_offline(): ...
"""

from __future__ import annotations

import hashlib
import logging
import socket
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("llm_cache")

# BASE_DIR = корень проекта (файл лежит в core/, поэтому .parent.parent)
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DB = DATA_DIR / "llm_cache.db"

PING_HOSTS = [("8.8.8.8", 53), ("1.1.1.1", 53), ("77.88.8.8", 53)]


# ══════════════════════════════════════════════════════════════════
# Инициализация БД
# ══════════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_hash TEXT    NOT NULL UNIQUE,
    prompt_text TEXT    NOT NULL,
    response    TEXT    NOT NULL,
    model       TEXT    NOT NULL DEFAULT 'unknown',
    agent       TEXT    NOT NULL DEFAULT 'unknown',
    hits        INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL DEFAULT (unixepoch()),
    expires_at  REAL    NOT NULL,
    last_hit    REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_hash    ON llm_cache(prompt_hash);
CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_cache(expires_at);

CREATE TABLE IF NOT EXISTS offline_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event       TEXT NOT NULL,
    details     TEXT,
    recorded_at REAL NOT NULL DEFAULT (unixepoch())
);
"""


def _init_db() -> None:
    try:
        with sqlite3.connect(str(CACHE_DB)) as conn:
            conn.executescript(_SCHEMA)
    except Exception as e:
        logger.error(f"Инициализация llm_cache.db: {e}")


_init_db()


# ══════════════════════════════════════════════════════════════════
# LLMCache
# ══════════════════════════════════════════════════════════════════

class LLMCache:
    """
    Потокобезопасный SQLite-кэш LLM-ответов с TTL.

    :param default_ttl: секунд (по умолчанию 24 часа)
    """

    def __init__(self, default_ttl: int = 86_400):
        self.default_ttl = default_ttl

    @staticmethod
    def _hash(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8", "ignore")).hexdigest()

    def get(self, prompt: str) -> Optional[str]:
        """Возвращает кэшированный ответ или None (если просрочен/нет)."""
        h = self._hash(prompt)
        now = time.time()
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                row = conn.execute(
                    "SELECT response, expires_at FROM llm_cache WHERE prompt_hash=?",
                    (h,),
                ).fetchone()
                if not row:
                    return None
                response, expires_at = row
                if expires_at < now:
                    conn.execute("DELETE FROM llm_cache WHERE prompt_hash=?", (h,))
                    return None
                conn.execute(
                    "UPDATE llm_cache SET hits=hits+1, last_hit=? WHERE prompt_hash=?",
                    (now, h),
                )
                return response
        except Exception as e:
            logger.warning(f"LLMCache.get: {e}")
            return None

    def set(self, prompt: str, response: str,
            ttl: Optional[int] = None,
            model: str = "unknown",
            agent: str = "unknown") -> None:
        """Сохраняет ответ в кэш."""
        h = self._hash(prompt)
        now = time.time()
        expires_at = now + (ttl if ttl is not None else self.default_ttl)
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                conn.execute(
                    """INSERT INTO llm_cache
                       (prompt_hash, prompt_text, response, model, agent,
                        created_at, expires_at, last_hit)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(prompt_hash) DO UPDATE SET
                         response=excluded.response,
                         expires_at=excluded.expires_at,
                         hits=llm_cache.hits+1,
                         last_hit=excluded.last_hit""",
                    (h, prompt[:2000], response, model, agent, now, expires_at, now),
                )
        except Exception as e:
            logger.warning(f"LLMCache.set: {e}")

    def delete(self, prompt: str) -> None:
        h = self._hash(prompt)
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                conn.execute("DELETE FROM llm_cache WHERE prompt_hash=?", (h,))
        except Exception:
            pass

    def evict_expired(self) -> int:
        """Удаляет просроченные записи. Возвращает число удалённых."""
        now = time.time()
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                cur = conn.execute("DELETE FROM llm_cache WHERE expires_at < ?", (now,))
                return cur.rowcount
        except Exception:
            return 0

    def stats(self) -> dict:
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                total = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
                hits = conn.execute("SELECT COALESCE(SUM(hits),0) FROM llm_cache").fetchone()[0]
                by_agent = conn.execute(
                    "SELECT agent, COUNT(*) FROM llm_cache GROUP BY agent"
                ).fetchall()
            return {
                "total_entries": total,
                "total_hits": hits,
                "by_agent": {a: c for a, c in by_agent},
            }
        except Exception as e:
            return {"error": str(e)}

    def clear(self) -> int:
        try:
            with sqlite3.connect(str(CACHE_DB)) as conn:
                cur = conn.execute("DELETE FROM llm_cache")
                return cur.rowcount
        except Exception:
            return 0


# ══════════════════════════════════════════════════════════════════
# OfflineDetector
# ══════════════════════════════════════════════════════════════════

class OfflineDetector:
    """Быстрая проверка интернет-доступа через TCP-пинг на 53 порт."""

    def __init__(self, cache_seconds: float = 30.0):
        self.cache_seconds = cache_seconds
        self._last_check = 0.0
        self._last_result = True

    def is_online(self) -> bool:
        now = time.time()
        if now - self._last_check < self.cache_seconds:
            return self._last_result
        for host, port in PING_HOSTS:
            try:
                with socket.create_connection((host, port), timeout=2):
                    self._last_result = True
                    self._last_check = now
                    return True
            except OSError:
                continue
        self._last_result = False
        self._last_check = now
        return False

    def is_offline(self) -> bool:
        return not self.is_online()
