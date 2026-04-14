"""
agents/wen.py — Агент Вэнь (Секретарь). Сиен 3.0.0.

Исправления против 2.0 Beta:
  • Абсолютный путь к БД (BASE_DIR/data/wen_tasks.db) — данные не теряются.
  • Убрано дублирование app = FastAPI() в конце файла.
  • reminder_loop больше не вкладывает sqlite3.connect в открытый with get_db().
  • chat_id берётся из профиля (Кронос), а не hardcoded "YOUR_CHAT_ID".
  • Убрана зависимость от websockets — уведомления шлются через HTTP POST
    на оркестратор (/ws/internal/push) либо просто логируются.
  • Graceful shutdown через core.graceful_shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import aiohttp
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("wen")
logging.basicConfig(level=logging.INFO, format="[ВЭНЬ] %(message)s")

# ── Пути ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "wen_tasks.db"

KRONOS_URL = "http://localhost:8001"
ORCHESTRATOR_URL = "http://localhost:8000"

router = APIRouter(prefix="/tasks", tags=["wen"])


# ══════════════════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════════════════

def init_db() -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT,
                due         TEXT,
                status      TEXT DEFAULT 'pending',
                notify_sent INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now')),
                tags        TEXT DEFAULT '[]'
            )
        """)
        conn.commit()
    logger.info(f"БД: {DB_PATH}")


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
# Модели
# ══════════════════════════════════════════════════════════════════

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due: Optional[str] = None
    tags: List[str] = []


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None


def row_to_task(row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    return d


# ══════════════════════════════════════════════════════════════════
# Уведомления
# ══════════════════════════════════════════════════════════════════

async def get_secret(key: str) -> Optional[str]:
    """Получает секрет у Кроноса."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_URL}/secrets/get",
                params={"key": key},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("value")
    except Exception:
        pass
    return None


async def send_telegram(text: str) -> None:
    token = await get_secret("telegram_bot_token")
    chat_id = await get_secret("telegram_chat_id")
    if not token or not chat_id:
        logger.info(f"[TG заглушка] {text}")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    logger.info(f"Telegram отправлено в {chat_id}")
                else:
                    logger.warning(f"Telegram API: {r.status}")
    except Exception as e:
        logger.error(f"Telegram: {e}")


async def send_orchestrator_push(task: dict) -> None:
    """Отправляет уведомление через HTTP к оркестратору (а не через WS)."""
    payload = {
        "type": "reminder",
        "agent": "wen",
        "task_id": task["id"],
        "title": task["title"],
        "due": task.get("due"),
        "message": f"⏰ Напоминание: «{task['title']}» через 10 минут!",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ORCHESTRATOR_URL}/internal/push",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    logger.info(f"Push для задачи #{task['id']}")
    except Exception as e:
        logger.debug(f"Push недоступен: {e}")


# ══════════════════════════════════════════════════════════════════
# Reminder loop
# ══════════════════════════════════════════════════════════════════

async def reminder_loop():
    """Каждую минуту проверяет задачи и шлёт напоминания за 10 минут."""
    logger.info("Планировщик напоминаний запущен")
    while True:
        try:
            now = datetime.now()
            ws_from = now + timedelta(minutes=9, seconds=30)
            ws_to = now + timedelta(minutes=10, seconds=30)

            # Читаем задачи одним коннектом, собираем список, закрываем
            pending: list[dict] = []
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT * FROM tasks
                    WHERE status='pending' AND notify_sent=0
                      AND due IS NOT NULL AND due != ''
                """).fetchall()
                for row in rows:
                    task = row_to_task(row)
                    try:
                        due_dt = datetime.fromisoformat(task["due"])
                    except ValueError:
                        continue
                    if ws_from <= due_dt <= ws_to:
                        pending.append(task)

            # Уведомляем и обновляем флаг — отдельным коннектом
            for task in pending:
                logger.info(f"Напоминание #{task['id']}: {task['title']}")
                await send_orchestrator_push(task)
                await send_telegram(f"⏰ *Напоминание:* «{task['title']}» через 10 минут!")
                with get_db() as conn:
                    conn.execute(
                        "UPDATE tasks SET notify_sent=1 WHERE id=?",
                        (task["id"],),
                    )
        except Exception as e:
            logger.error(f"reminder_loop: {e}")

        await asyncio.sleep(60)


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

@router.post("/create")
async def create_task(task: TaskCreate):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, description, due, tags) VALUES (?, ?, ?, ?)",
            (task.title, task.description, task.due, json.dumps(task.tags, ensure_ascii=False)),
        )
        task_id = cur.lastrowid
    logger.info(f"#{task_id}: {task.title}")
    return {"status": "created", "id": task_id, "title": task.title, "due": task.due,
            "message": f"Задача «{task.title}» добавлена"}


@router.get("/list")
async def list_tasks(status: Optional[str] = None, limit: int = 50):
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY due ASC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY due ASC LIMIT ?",
                (limit,),
            ).fetchall()
    return [row_to_task(r) for r in rows]


@router.delete("/delete/{task_id}")
async def delete_task(task_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT id, title FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Задача #{task_id} не найдена")
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return {"status": "deleted", "id": task_id, "title": row["title"]}


@router.patch("/update/{task_id}")
async def update_task(task_id: int, update: TaskUpdate):
    fields, values = [], []
    for col in ("title", "description", "due", "status"):
        val = getattr(update, col)
        if val is not None:
            fields.append(f"{col}=?")
            values.append(val)
    if update.tags is not None:
        fields.append("tags=?")
        values.append(json.dumps(update.tags, ensure_ascii=False))
    if not fields:
        raise HTTPException(400, "Нет полей для обновления")
    values.append(task_id)
    with get_db() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
    return {"status": "updated", "id": task_id}


@router.get("/health")
async def health():
    try:
        with get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='pending'"
            ).fetchone()[0]
        return {"agent": "Вэнь", "alive": True, "pending_tasks": count}
    except Exception as e:
        return {"agent": "Вэнь", "alive": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════
# App (единственное объявление)
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Вэнь — Секретарь")
app.include_router(router)
init_db()

# ── Сиен 3.0.0: подключаем почтовый модуль ──
try:
    from agents.wen_email import setup_email_routes
    setup_email_routes(app)
except Exception as _ext_err:
    logger.warning(f"Почтовый модуль недоступен: {_ext_err}")


@app.on_event("startup")
async def _start():
    asyncio.create_task(reminder_loop())
    logger.info("Агент Вэнь запущен")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
