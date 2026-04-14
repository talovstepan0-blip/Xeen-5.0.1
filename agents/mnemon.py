"""
agents/mnemon.py — Агент Мнемон (Переводчик). Сиен 3.0.0.

Изменения:
  • Добавлен app = FastAPI().
  • Абсолютный путь к БД кэша.
  • Бэкенды: offline (Helsinki-NLP через transformers) | online (HTTP стаб) | cache-only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("mnemon")
logging.basicConfig(level=logging.INFO, format="[МНЕМОН] %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "mnemon_cache.db"

router = APIRouter(prefix="/translate", tags=["mnemon"])

BACKEND = os.environ.get("MNEMON_BACKEND", "auto")  # auto | offline | online | cache

# ── Опциональные модели ──────────────────────────────────────────
try:
    from transformers import pipeline  # type: ignore
    TRANSFORMERS_OK = True
except ImportError:
    TRANSFORMERS_OK = False

_pipelines: dict[tuple[str, str], object] = {}


def _get_pipeline(src: str, tgt: str):
    """Ленивая загрузка Helsinki-NLP моделей (offline)."""
    if not TRANSFORMERS_OK:
        return None
    key = (src, tgt)
    if key in _pipelines:
        return _pipelines[key]
    model_name = f"Helsinki-NLP/opus-mt-{src}-{tgt}"
    try:
        p = pipeline("translation", model=model_name)
        _pipelines[key] = p
        return p
    except Exception as e:
        logger.warning(f"Модель {model_name}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# БД кэша
# ══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS translation_cache (
    hash        TEXT PRIMARY KEY,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    source_text TEXT NOT NULL,
    translated  TEXT NOT NULL,
    backend     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _hash(src: str, tgt: str, text: str) -> str:
    return hashlib.sha256(f"{src}::{tgt}::{text}".encode()).hexdigest()


# ══════════════════════════════════════════════════════════════════
# Модели
# ══════════════════════════════════════════════════════════════════

class TranslateRequest(BaseModel):
    text: str
    source_lang: str = "auto"
    target_lang: str = "ru"


class BatchRequest(BaseModel):
    texts: list[str]
    source_lang: str = "en"
    target_lang: str = "ru"


# ══════════════════════════════════════════════════════════════════
# Перевод
# ══════════════════════════════════════════════════════════════════

def translate_one(text: str, src: str, tgt: str) -> tuple[str, str]:
    """Возвращает (перевод, backend)."""
    if src == "auto":
        src = "en"  # заглушка детекции языка

    key = _hash(src, tgt, text)
    with get_db() as conn:
        row = conn.execute(
            "SELECT translated, backend FROM translation_cache WHERE hash=?",
            (key,),
        ).fetchone()
        if row:
            return row["translated"], f"cache:{row['backend']}"

    translated: Optional[str] = None
    backend_used = "stub"

    if BACKEND in ("auto", "offline") and TRANSFORMERS_OK:
        p = _get_pipeline(src, tgt)
        if p is not None:
            try:
                result = p(text)
                translated = result[0]["translation_text"]  # type: ignore
                backend_used = "helsinki"
            except Exception as e:
                logger.warning(f"Helsinki: {e}")

    if translated is None:
        # Заглушка
        translated = f"[{src}→{tgt}] {text}"
        backend_used = "stub"

    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO translation_cache
               (hash, source_lang, target_lang, source_text, translated, backend)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key, src, tgt, text, translated, backend_used),
        )

    return translated, backend_used


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

@router.post("")
@router.post("/")
async def translate(req: TranslateRequest):
    if not req.text.strip():
        raise HTTPException(400, "Пустой текст")
    translated, backend = translate_one(req.text, req.source_lang, req.target_lang)
    return {
        "source_lang": req.source_lang,
        "target_lang": req.target_lang,
        "source_text": req.text,
        "translated": translated,
        "backend": backend,
    }


@router.post("/batch")
async def translate_batch(req: BatchRequest):
    results = [
        {"source": t, "translated": translate_one(t, req.source_lang, req.target_lang)[0]}
        for t in req.texts
    ]
    return {"results": results, "count": len(results)}


@router.get("/cache/stats")
def cache_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM translation_cache").fetchone()[0]
        by_backend = dict(conn.execute(
            "SELECT backend, COUNT(*) FROM translation_cache GROUP BY backend"
        ).fetchall())
        by_lang = dict(conn.execute(
            "SELECT source_lang, COUNT(*) FROM translation_cache GROUP BY source_lang"
        ).fetchall())
    return {
        "total_cached": total,
        "by_backend": by_backend,
        "by_source_lang": by_lang,
        "current_backend": BACKEND,
    }


@router.delete("/cache/clear")
def cache_clear():
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM translation_cache").fetchone()[0]
        conn.execute("DELETE FROM translation_cache")
    return {"status": "cleared", "deleted_entries": count}


@router.get("/health")
def health():
    return {
        "agent": "Мнемон", "alive": True,
        "backend": BACKEND,
        "transformers": TRANSFORMERS_OK,
    }


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

init_db()
app = FastAPI(title="Мнемон — Переводчик")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8021)
