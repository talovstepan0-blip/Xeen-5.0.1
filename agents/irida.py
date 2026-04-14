"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Ирида' (Telegram) — постинг и управление через Telegram Bot API.
Этап 4 проекта 'Сиен 01'.

Токен бота получает из агента Кронос (если доступен) или из env TELEGRAM_BOT_TOKEN.
Поддерживает: отправку текста, фото, документов; управление чатами; вебхук.
"""

import os, logging, sqlite3, asyncio
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("irida")
logging.basicConfig(level=logging.INFO, format="[ИРИДА] %(message)s")

router   = APIRouter(prefix="/telegram", tags=["irida"])
DB_PATH  = str(_DATA_DIR / "irida.db")
KRONOS_URL = os.environ.get("KRONOS_URL", "http://localhost:8001")

Path("data").mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     TEXT NOT NULL,
            text        TEXT,
            msg_type    TEXT DEFAULT 'text',
            tg_msg_id   INTEGER,
            status      TEXT DEFAULT 'sent',
            sent_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS chats (
            chat_id     TEXT PRIMARY KEY,
            title       TEXT,
            type        TEXT,
            added_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS scheduled (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     TEXT NOT NULL,
            text        TEXT NOT NULL,
            send_at     TEXT NOT NULL,
            sent        INTEGER DEFAULT 0
        );
    """)
    conn.commit(); conn.close()

init_db()

# ── Получение токена ──────────────────────────────────────────────────────────

async def get_token() -> str:
    """Токен из env или из Кроноса."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        return token
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{KRONOS_URL}/secret/telegram_bot_token",
                            headers={"x-agent-name": "irida", "x-agent-token": "token-irida-alpha"})
            if r.status_code == 200:
                return r.json().get("value", "")
    except Exception:
        pass
    raise HTTPException(503, "Токен Telegram не настроен. Установи TELEGRAM_BOT_TOKEN или добавь в Кронос.")

async def tg_request(method: str, payload: dict) -> dict:
    """Выполнить запрос к Telegram Bot API."""
    token = await get_token()
    url   = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json=payload)
            data = r.json()
            if not data.get("ok"):
                raise HTTPException(400, f"Telegram API ошибка: {data.get('description', 'Unknown')}")
            return data.get("result", {})
    except httpx.TimeoutException:
        raise HTTPException(504, "Telegram API не ответил")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Ошибка Telegram API: {e}")

def log_message(chat_id: str, text: str, msg_type: str, tg_msg_id: Optional[int], status: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO messages (chat_id, text, msg_type, tg_msg_id, status)
        VALUES (?,?,?,?,?)
    """, (chat_id, text, msg_type, tg_msg_id, status))
    conn.commit(); conn.close()

# ── Модели ────────────────────────────────────────────────────────────────────

class SendReq(BaseModel):
    chat_id: str
    text: str
    parse_mode: str = "Markdown"    # Markdown | HTML | None
    disable_preview: bool = False

class SendPhotoReq(BaseModel):
    chat_id: str
    photo_url: str
    caption: Optional[str] = None
    parse_mode: str = "Markdown"

class SendDocReq(BaseModel):
    chat_id: str
    document_url: str
    caption: Optional[str] = None

class ForwardReq(BaseModel):
    from_chat_id: str
    to_chat_id: str
    message_id: int

class ScheduleReq(BaseModel):
    chat_id: str
    text: str
    send_at: str    # ISO datetime: "2025-01-15T18:00:00"

class BroadcastReq(BaseModel):
    text: str
    parse_mode: str = "Markdown"

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/send")
async def send(req: SendReq):
    """Отправить текстовое сообщение."""
    payload = {
        "chat_id": req.chat_id,
        "text": req.text,
        "parse_mode": req.parse_mode,
        "disable_web_page_preview": req.disable_preview,
    }
    result = await tg_request("sendMessage", payload)
    msg_id = result.get("message_id")
    log_message(req.chat_id, req.text, "text", msg_id, "sent")
    logger.info(f"Сообщение отправлено в {req.chat_id}, msg_id={msg_id}")
    return {"status": "sent", "message_id": msg_id, "chat_id": req.chat_id}

@router.post("/send_photo")
async def send_photo(req: SendPhotoReq):
    """Отправить фото по URL."""
    result = await tg_request("sendPhoto", {
        "chat_id": req.chat_id, "photo": req.photo_url,
        "caption": req.caption, "parse_mode": req.parse_mode,
    })
    msg_id = result.get("message_id")
    log_message(req.chat_id, req.caption or "", "photo", msg_id, "sent")
    return {"status": "sent", "message_id": msg_id}

@router.post("/send_document")
async def send_document(req: SendDocReq):
    """Отправить документ по URL."""
    result = await tg_request("sendDocument", {
        "chat_id": req.chat_id, "document": req.document_url,
        "caption": req.caption,
    })
    return {"status": "sent", "message_id": result.get("message_id")}

@router.post("/forward")
async def forward(req: ForwardReq):
    """Переслать сообщение."""
    result = await tg_request("forwardMessage", {
        "chat_id": req.to_chat_id, "from_chat_id": req.from_chat_id,
        "message_id": req.message_id,
    })
    return {"status": "forwarded", "message_id": result.get("message_id")}

@router.post("/broadcast")
async def broadcast(req: BroadcastReq):
    """Отправить сообщение всем сохранённым чатам."""
    conn = sqlite3.connect(DB_PATH)
    chats = [r[0] for r in conn.execute("SELECT chat_id FROM chats").fetchall()]
    conn.close()
    if not chats:
        return {"status": "no_chats", "sent": 0}
    results = []
    for chat_id in chats:
        try:
            r = await tg_request("sendMessage", {
                "chat_id": chat_id, "text": req.text, "parse_mode": req.parse_mode
            })
            results.append({"chat_id": chat_id, "ok": True, "msg_id": r.get("message_id")})
        except Exception as e:
            results.append({"chat_id": chat_id, "ok": False, "error": str(e)})
    return {"sent": sum(1 for r in results if r["ok"]), "results": results}

@router.post("/schedule")
def schedule_message(req: ScheduleReq):
    """Запланировать отправку сообщения."""
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute(
        "INSERT INTO scheduled (chat_id, text, send_at) VALUES (?,?,?)",
        (req.chat_id, req.text, req.send_at)
    )
    sched_id = cur.lastrowid
    conn.commit(); conn.close()
    return {"status": "scheduled", "id": sched_id, "send_at": req.send_at}

@router.get("/me")
async def get_me():
    """Информация о боте."""
    return await tg_request("getMe", {})

@router.get("/updates")
async def get_updates(offset: Optional[int] = None, limit: int = 10):
    """Получить последние обновления (входящие сообщения)."""
    payload = {"limit": limit, "timeout": 0}
    if offset:
        payload["offset"] = offset
    updates = await tg_request("getUpdates", payload)
    return {"updates": updates if isinstance(updates, list) else []}

@router.post("/add_chat")
def add_chat(chat_id: str, title: Optional[str] = None, type: str = "group"):
    """Добавить чат в список для broadcast."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO chats (chat_id, title, type) VALUES (?,?,?)",
                 (chat_id, title, type))
    conn.commit(); conn.close()
    return {"status": "added", "chat_id": chat_id}

@router.get("/history")
def message_history(chat_id: Optional[str] = None, limit: int = 50):
    """История отправленных сообщений."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if chat_id:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY sent_at DESC LIMIT ?", (chat_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return {"messages": [dict(r) for r in rows]}

# ── Планировщик ───────────────────────────────────────────────────────────────

async def scheduler_loop():
    """Фоновая задача: отправляет запланированные сообщения."""
    logger.info("Планировщик Ирида запущен")
    while True:
        from datetime import datetime
        now = datetime.now().isoformat()
        conn = sqlite3.connect(DB_PATH)
        pending = conn.execute(
            "SELECT * FROM scheduled WHERE sent=0 AND send_at <= ?", (now,)
        ).fetchall()
        conn.close()
        for row in pending:
            try:
                await tg_request("sendMessage", {"chat_id": row[1], "text": row[2]})
                conn2 = sqlite3.connect(DB_PATH)
                conn2.execute("UPDATE scheduled SET sent=1 WHERE id=?", (row[0],))
                conn2.commit(); conn2.close()
                logger.info(f"Запланированное сообщение #{row[0]} отправлено")
            except Exception as e:
                logger.error(f"Ошибка отправки запланированного #{row[0]}: {e}")
        await asyncio.sleep(30)

@router.get("/health")
async def health():
    try:
        me = await tg_request("getMe", {})
        return {"agent": "Ирида", "alive": True, "bot": me.get("username"), "token_ok": True}
    except Exception:
        return {"agent": "Ирида", "alive": True, "token_ok": False,
                "note": "Установи TELEGRAM_BOT_TOKEN"}

app = FastAPI(title="Ирида — Telegram")
app.include_router(router)

@app.on_event("startup")
async def startup():
    asyncio.create_task(scheduler_loop())
    logger.info("Агент Ирида запущен.")

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8017)
