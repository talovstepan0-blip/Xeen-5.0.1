"""
agents/argus.py — Агент Аргус (Монитор). Сиен 3.0.0.

Главное изменение: был чистый asyncio-скрипт без FastAPI.
Теперь — полноценный сервис с app, router и /monitor/* эндпоинтами.

Возможности:
  • Периодический ping /health всех агентов.
  • Счётчик неудач на агента, >= MAX_FAILURES → событие в лог и опциональный перезапуск.
  • /monitor/status — текущее состояние всех агентов.
  • /monitor/log — последние события из sien.db.
  • /monitor/search?query=... — поиск в DuckDuckGo (используется оркестратором).
  • /monitor/health — свой health.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import aiohttp
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

logger = logging.getLogger("argus")
logging.basicConfig(level=logging.INFO, format="[АРГУС] %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sien.db"

router = APIRouter(prefix="/monitor", tags=["argus"])

# ══════════════════════════════════════════════════════════════════
# Конфигурация
# ══════════════════════════════════════════════════════════════════

AGENTS: list[dict] = [
    {"name": "orchestrator", "url": "http://localhost:8000/health"},
    {"name": "cronos",       "url": "http://localhost:8001/health"},
    {"name": "ahill",        "url": "http://localhost:8003/proxy/health"},
    {"name": "fenix",        "url": "http://localhost:8004/fenix/health"},
    {"name": "logos",        "url": "http://localhost:8005/logos/health"},
    {"name": "wen",          "url": "http://localhost:8006/tasks/health"},
    {"name": "kun",          "url": "http://localhost:8007/professor/health"},
    {"name": "master",       "url": "http://localhost:8008/trainer/health"},
    {"name": "plutos",       "url": "http://localhost:8009/invest/health"},
    {"name": "musa",         "url": "http://localhost:8010/content/health"},
    {"name": "kallio",       "url": "http://localhost:8011/media/health"},
    {"name": "hefest",       "url": "http://localhost:8012/code/health"},
    {"name": "avto",         "url": "http://localhost:8013/macros/health"},
    {"name": "eho",          "url": "http://localhost:8016/tts/health"},
    {"name": "irida",        "url": "http://localhost:8017/telegram/health"},
    {"name": "apollo",       "url": "http://localhost:8018/video/health"},
    {"name": "hermes",       "url": "http://localhost:8019/affiliate/health"},
    {"name": "dike",         "url": "http://localhost:8020/accounting/health"},
    {"name": "mnemon",       "url": "http://localhost:8021/translate/health"},
]

CHECK_INTERVAL = int(os.environ.get("ARGUS_CHECK_INTERVAL", "30"))
MAX_FAILURES = int(os.environ.get("ARGUS_MAX_FAILURES", "3"))


# ══════════════════════════════════════════════════════════════════
# State
# ══════════════════════════════════════════════════════════════════

class ArgusState:
    def __init__(self):
        self.statuses: dict[str, dict] = {}
        self.failure_counts: dict[str, int] = {a["name"]: 0 for a in AGENTS}


state = ArgusState()


# ══════════════════════════════════════════════════════════════════
# БД логов
# ══════════════════════════════════════════════════════════════════

@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def db_log(level: str, source: str, message: str) -> None:
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO logs (level, source, message)
                   VALUES (?, ?, ?)""",
                (level, source, message),
            )
    except sqlite3.OperationalError:
        # Таблица logs может не существовать до init_db.py
        pass
    except Exception as e:
        logger.debug(f"db_log: {e}")


# ══════════════════════════════════════════════════════════════════
# Мониторинг
# ══════════════════════════════════════════════════════════════════

async def check_agent(session: aiohttp.ClientSession, agent: dict) -> dict:
    name = agent["name"]
    try:
        async with session.get(
            agent["url"], timeout=aiohttp.ClientTimeout(total=3)
        ) as r:
            if r.status == 200:
                data = await r.json()
                state.failure_counts[name] = 0
                result = {
                    "name": name,
                    "status": "ok",
                    "alive": data.get("alive", True),
                    "checked_at": datetime.now().isoformat(),
                }
                state.statuses[name] = result
                return result
    except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
        pass
    except Exception as e:
        logger.debug(f"check_agent {name}: {e}")

    state.failure_counts[name] += 1
    status = "offline" if state.failure_counts[name] >= MAX_FAILURES else "unstable"
    result = {
        "name": name,
        "status": status,
        "failures": state.failure_counts[name],
        "checked_at": datetime.now().isoformat(),
    }
    state.statuses[name] = result

    if state.failure_counts[name] == MAX_FAILURES:
        db_log("WARNING", name, f"{MAX_FAILURES} неудачных проверок подряд")
        logger.warning(f"{name}: {MAX_FAILURES} failures")

    return result


async def monitor_loop():
    logger.info(f"Аргус запущен. Интервал: {CHECK_INTERVAL}с")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await asyncio.gather(
                    *(check_agent(session, a) for a in AGENTS),
                    return_exceptions=True,
                )
            except Exception as e:
                logger.error(f"monitor_loop: {e}")
            await asyncio.sleep(CHECK_INTERVAL)


# ══════════════════════════════════════════════════════════════════
# DuckDuckGo поиск
# ══════════════════════════════════════════════════════════════════

class SearchRequest(BaseModel):
    query: str
    max_results: int = 5


@router.post("/search")
async def search_web(req: SearchRequest):
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://html.duckduckgo.com/html/",
                params={"q": req.query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                text = await r.text()
        titles = re.findall(
            r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', text, re.DOTALL)
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>', text, re.DOTALL)
        for i in range(min(req.max_results, len(titles))):
            title = re.sub(r'<[^>]+>', '', titles[i]).strip()
            snippet = (re.sub(r'<[^>]+>', '', snippets[i]).strip()
                       if i < len(snippets) else "")
            if title:
                results.append({"title": title, "snippet": snippet})
    except Exception as e:
        logger.warning(f"DDG: {e}")
    return {"query": req.query, "results": results, "count": len(results)}


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

@router.get("/status")
def status_all():
    """Текущее состояние всех агентов."""
    return {
        "agents": list(state.statuses.values()),
        "total": len(AGENTS),
        "online": sum(1 for s in state.statuses.values() if s.get("status") == "ok"),
        "offline": sum(1 for s in state.statuses.values()
                       if s.get("status") == "offline"),
        "check_interval_sec": CHECK_INTERVAL,
    }


@router.get("/log")
def get_log(level: str = "", limit: int = 50):
    """Последние события из sien.db.logs."""
    try:
        with get_db() as conn:
            if level:
                rows = conn.execute(
                    """SELECT level, source, message, ts FROM logs
                       WHERE level=? ORDER BY ts DESC LIMIT ?""",
                    (level, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT level, source, message, ts FROM logs
                       ORDER BY ts DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return {"events": [dict(r) for r in rows], "count": len(rows)}
    except sqlite3.OperationalError:
        return {"events": [], "count": 0, "warning": "logs table not found"}


@router.get("/health")
def health():
    return {
        "agent": "Аргус", "alive": True,
        "monitoring": len(AGENTS),
        "failures": sum(state.failure_counts.values()),
    }


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Аргус — Монитор")
app.include_router(router)


@app.on_event("startup")
async def _start():
    asyncio.create_task(monitor_loop())
    logger.info("Агент Аргус запущен")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8022)
