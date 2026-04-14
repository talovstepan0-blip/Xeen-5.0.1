"""
agents/hermes.py — Агент Гермес (Affiliate). Сиен 3.0.0.

Изменения против 2.0 Beta:
  • Добавлен app = FastAPI() (раньше падал с "Attribute app not found").
  • Абсолютный путь к БД.
  • Новая таблица trends + эндпоинты /affiliate/trends/fetch и /affiliate/trends/list
    (Telethon + VK API, опциональные импорты).
  • A/B тестирование: /affiliate/ab/generate (3 варианта) и /affiliate/ab/compare.
  • Интеграция с Яндекс.Метрикой: /affiliate/metrics.
  • Все операции с sqlite3 — через контекстный менеджер.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("hermes")
logging.basicConfig(level=logging.INFO, format="[ГЕРМЕС] %(message)s")

# ── Пути ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "hermes.db"

KRONOS_URL = "http://localhost:8001"

# ── Опциональные зависимости ─────────────────────────────────────
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import LabelEncoder
    import numpy as np
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False

try:
    from telethon import TelegramClient  # type: ignore
    TELETHON_OK = True
except ImportError:
    TELETHON_OK = False

try:
    import vk_api  # type: ignore
    VK_OK = True
except ImportError:
    VK_OK = False


router = APIRouter(prefix="/affiliate", tags=["hermes"])


# ══════════════════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   TEXT UNIQUE,
    name         TEXT,
    price        REAL,
    category     TEXT,
    affiliate_url TEXT,
    active       INTEGER DEFAULT 1,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   TEXT,
    channel      TEXT,
    text         TEXT,
    image_url    TEXT,
    variant      TEXT DEFAULT 'A',
    ab_group     TEXT,
    published_at TEXT,
    clicks       INTEGER DEFAULT 0,
    purchases    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channels (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT UNIQUE,
    chat_id TEXT,
    platform TEXT DEFAULT 'telegram',
    active  INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trends (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,            -- telegram | vk
    keyword     TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    score       INTEGER DEFAULT 0,
    fetched_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trends_keyword ON trends(keyword);
CREATE INDEX IF NOT EXISTS idx_trends_fetched ON trends(fetched_at DESC);

CREATE TABLE IF NOT EXISTS ab_tests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    variants    TEXT NOT NULL,            -- JSON [{"label":"A","text":...}, ...]
    created_at  TEXT DEFAULT (datetime('now')),
    winner      TEXT,
    finished_at TEXT
);

INSERT OR IGNORE INTO channels (name, chat_id) VALUES ('main', '@sien01_shop');
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


# ══════════════════════════════════════════════════════════════════
# Помощники
# ══════════════════════════════════════════════════════════════════

async def get_secret(key: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_URL}/secrets/get",
                params={"key": key},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    return (await r.json()).get("value")
    except Exception:
        pass
    return None


def fetch_products_stub(category: str = "electronics", limit: int = 5) -> list[dict]:
    return [
        {"id": f"ym_{i}", "name": f"Товар {category} #{i}",
         "price": round(random.uniform(500, 15000), 2),
         "category": category,
         "rating": round(random.uniform(3.5, 5.0), 1),
         "sales_count": random.randint(10, 5000)}
        for i in range(1, limit + 1)
    ]


def make_affiliate_url(product_id: str) -> str:
    return f"https://clck.ru/fake_{product_id[:8]}"


def llm_select_products(products: list[dict]) -> list[dict]:
    return [p for p in products if p["rating"] >= 4.0 and p["sales_count"] >= 100]


def llm_generate_post(product: dict, style: str = "default") -> str:
    """Генерация текста поста. style = default | energetic | minimal."""
    if style == "energetic":
        return (
            f"🔥🔥 ВЗРЫВ ЦЕН! 🔥🔥\n\n"
            f"{product['name']}\n"
            f"💎 Всего за {product['price']} ₽\n"
            f"⭐ {product.get('rating', 4.5)}/5\n\n"
            f"БЕГОМ → {product.get('affiliate_url', '')}\n"
            f"#огонь #{product['category']}"
        )
    if style == "minimal":
        return (
            f"{product['name']} — {product['price']} ₽\n"
            f"{product.get('affiliate_url', '')}"
        )
    return (
        f"🔥 {product['name']}\n\n"
        f"💰 Цена: {product['price']} ₽\n"
        f"⭐ Рейтинг: {product.get('rating', 4.5)}/5\n\n"
        f"Отличное предложение!\n\n"
        f"👉 {product.get('affiliate_url', '')}\n\n"
        f"#партнёрка #{product['category']}"
    )


# ══════════════════════════════════════════════════════════════════
# Парсинг трендов: Telegram (Telethon) + VK
# ══════════════════════════════════════════════════════════════════

async def fetch_trends_telegram(keywords: list[str], channels: list[str],
                                 limit: int = 20) -> list[dict]:
    """Читает последние сообщения из публичных каналов через Telethon."""
    if not TELETHON_OK:
        logger.info("Telethon не установлен — тренды из TG пропущены")
        return []

    api_id = await get_secret("telegram_api_id")
    api_hash = await get_secret("telegram_api_hash")
    if not api_id or not api_hash:
        logger.info("Нет telegram_api_id/hash в Кроносе")
        return []

    session_path = str(DATA_DIR / "hermes_tg.session")
    results: list[dict] = []
    try:
        client = TelegramClient(session_path, int(api_id), api_hash)
        await client.start()
        for channel in channels:
            try:
                async for msg in client.iter_messages(channel, limit=limit):
                    text = (msg.message or "").lower()
                    for kw in keywords:
                        if kw.lower() in text:
                            results.append({
                                "source": "telegram",
                                "keyword": kw,
                                "title": (msg.message or "")[:140],
                                "url": f"https://t.me/{channel.lstrip('@')}/{msg.id}",
                                "score": (msg.views or 0) + (msg.forwards or 0) * 5,
                            })
                            break
            except Exception as e:
                logger.warning(f"TG {channel}: {e}")
        await client.disconnect()
    except Exception as e:
        logger.error(f"Telethon: {e}")
    return results


async def fetch_trends_vk(keywords: list[str], limit: int = 20) -> list[dict]:
    """Поиск постов по ключевикам через VK API (newsfeed.search)."""
    if not VK_OK:
        logger.info("vk_api не установлен — тренды из VK пропущены")
        return []
    token = await get_secret("vk_access_token")
    if not token:
        logger.info("Нет vk_access_token в Кроносе")
        return []

    results: list[dict] = []
    try:
        session = vk_api.VkApi(token=token)
        api = session.get_api()
        for kw in keywords:
            try:
                data = api.newsfeed.search(q=kw, count=limit, extended=0)
                for item in data.get("items", []):
                    results.append({
                        "source": "vk",
                        "keyword": kw,
                        "title": (item.get("text") or "")[:140],
                        "url": f"https://vk.com/wall{item.get('owner_id')}_{item.get('id')}",
                        "score": item.get("likes", {}).get("count", 0)
                               + item.get("reposts", {}).get("count", 0) * 3,
                    })
            except Exception as e:
                logger.warning(f"VK '{kw}': {e}")
    except Exception as e:
        logger.error(f"vk_api: {e}")
    return results


def save_trends(trends: list[dict]) -> int:
    if not trends:
        return 0
    with get_db() as conn:
        conn.executemany(
            """INSERT INTO trends (source, keyword, title, url, score)
               VALUES (?, ?, ?, ?, ?)""",
            [(t["source"], t["keyword"], t.get("title", ""),
              t.get("url", ""), t.get("score", 0)) for t in trends],
        )
    return len(trends)


# ══════════════════════════════════════════════════════════════════
# Эндпоинты: тренды
# ══════════════════════════════════════════════════════════════════

class TrendsFetchRequest(BaseModel):
    keywords: list[str]
    telegram_channels: list[str] = []


@router.post("/trends/fetch")
async def trends_fetch(req: TrendsFetchRequest):
    """Параллельно тянет тренды из TG и VK, сохраняет в БД."""
    tg_task = fetch_trends_telegram(req.keywords, req.telegram_channels)
    vk_task = fetch_trends_vk(req.keywords)
    tg_results, vk_results = await asyncio.gather(tg_task, vk_task)
    total = save_trends(tg_results + vk_results)
    return {
        "telegram_count": len(tg_results),
        "vk_count": len(vk_results),
        "saved": total,
        "telethon_available": TELETHON_OK,
        "vk_available": VK_OK,
    }


@router.get("/trends/list")
def trends_list(keyword: Optional[str] = None, limit: int = 50):
    with get_db() as conn:
        if keyword:
            rows = conn.execute(
                """SELECT * FROM trends WHERE keyword=?
                   ORDER BY score DESC LIMIT ?""",
                (keyword, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM trends
                   ORDER BY fetched_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return {"trends": [dict(r) for r in rows], "count": len(rows)}


# ══════════════════════════════════════════════════════════════════
# Эндпоинты: A/B тестирование
# ══════════════════════════════════════════════════════════════════

class ABGenerateRequest(BaseModel):
    product_id: str
    category: str = "electronics"
    price: float = 1000.0
    name: str = ""


@router.post("/ab/generate")
def ab_generate(req: ABGenerateRequest):
    """Создаёт 3 варианта поста (A, B, C) для A/B-теста."""
    product = {
        "id": req.product_id, "name": req.name or f"Товар {req.product_id}",
        "price": req.price, "category": req.category,
        "affiliate_url": make_affiliate_url(req.product_id), "rating": 4.7,
    }
    variants = [
        {"label": "A", "style": "default",   "text": llm_generate_post(product, "default")},
        {"label": "B", "style": "energetic", "text": llm_generate_post(product, "energetic")},
        {"label": "C", "style": "minimal",   "text": llm_generate_post(product, "minimal")},
    ]
    group_id = f"ab_{req.product_id}_{int(datetime.now().timestamp())}"
    with get_db() as conn:
        conn.execute(
            """INSERT INTO ab_tests (product_id, group_id, variants)
               VALUES (?, ?, ?)""",
            (req.product_id, group_id, json.dumps(variants, ensure_ascii=False)),
        )
    return {"group_id": group_id, "variants": variants}


@router.post("/ab/compare/{group_id}")
def ab_compare(group_id: str):
    """Сравнивает CTR через 24 часа и выбирает победителя."""
    with get_db() as conn:
        test = conn.execute(
            "SELECT * FROM ab_tests WHERE group_id=?", (group_id,)
        ).fetchone()
        if not test:
            raise HTTPException(404, "A/B-тест не найден")

        rows = conn.execute(
            """SELECT variant, SUM(clicks) AS clicks, SUM(purchases) AS purchases
               FROM posts WHERE ab_group=? GROUP BY variant""",
            (group_id,),
        ).fetchall()

    stats = {r["variant"] or "A": {
        "clicks": r["clicks"] or 0,
        "purchases": r["purchases"] or 0,
        "ctr": (r["clicks"] or 0),
    } for r in rows}

    winner = max(stats.items(), key=lambda kv: kv[1]["ctr"])[0] if stats else None
    if winner:
        with get_db() as conn:
            conn.execute(
                """UPDATE ab_tests SET winner=?, finished_at=datetime('now')
                   WHERE group_id=?""",
                (winner, group_id),
            )

    return {"group_id": group_id, "stats": stats, "winner": winner}


# ══════════════════════════════════════════════════════════════════
# Эндпоинты: Яндекс.Метрика
# ══════════════════════════════════════════════════════════════════

@router.get("/metrics")
async def yandex_metrics(counter_id: Optional[str] = None,
                         metric: str = "ym:s:visits"):
    """Получает метрики из Яндекс.Метрики через API Management."""
    token = await get_secret("yandex_metrika_token")
    cid = counter_id or await get_secret("yandex_metrika_counter_id")
    if not token or not cid:
        return {"error": "Нет токена или counter_id в Кроносе",
                "configured": False}

    url = "https://api-metrika.yandex.net/stat/v1/data"
    params = {
        "ids": cid,
        "metrics": metric,
        "date1": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
        "date2": datetime.now().strftime("%Y-%m-%d"),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params,
                headers={"Authorization": f"OAuth {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return await r.json()
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# Эндпоинты: статистика, каналы
# ══════════════════════════════════════════════════════════════════

class AddChannelRequest(BaseModel):
    name: str
    chat_id: str
    platform: str = "telegram"


@router.get("/stats")
def get_stats():
    with get_db() as conn:
        total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        total_clicks = conn.execute(
            "SELECT COALESCE(SUM(clicks),0) FROM posts").fetchone()[0]
        total_purchases = conn.execute(
            "SELECT COALESCE(SUM(purchases),0) FROM posts").fetchone()[0]
        top = conn.execute(
            """SELECT product_id, SUM(purchases) AS conv
               FROM posts GROUP BY product_id ORDER BY conv DESC LIMIT 5"""
        ).fetchall()
    return {
        "total_posts": total_posts,
        "total_clicks": total_clicks,
        "total_purchases": total_purchases,
        "conversion_rate": round(total_purchases / max(total_clicks, 1) * 100, 2),
        "top_products": [{"product_id": r["product_id"],
                          "purchases": r["conv"]} for r in top],
    }


@router.post("/stop/{product_id}")
def stop_product(product_id: str):
    with get_db() as conn:
        conn.execute("UPDATE products SET active=0 WHERE product_id=?",
                     (product_id,))
    return {"status": "stopped", "product_id": product_id}


@router.post("/add_channel")
def add_channel(req: AddChannelRequest):
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO channels (name, chat_id, platform)
                   VALUES (?, ?, ?)""",
                (req.name, req.chat_id, req.platform),
            )
        return {"status": "added", "name": req.name, "chat_id": req.chat_id}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Канал уже существует")


@router.post("/simulate_click/{post_id}")
def simulate_click(post_id: int):
    with get_db() as conn:
        conn.execute("UPDATE posts SET clicks=clicks+1 WHERE id=?", (post_id,))
    return {"status": "ok"}


@router.get("/health")
def health():
    return {
        "agent": "Гермес", "alive": True,
        "sklearn": SKLEARN_OK,
        "telethon": TELETHON_OK,
        "vk_api": VK_OK,
    }


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

init_db()
app = FastAPI(title="Гермес — Affiliate")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8019)
