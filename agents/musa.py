"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Муса' (Контент) — генерация и публикация постов.
Этап 4 проекта 'Сиен 01'.

LLM-генерация текста + заглушки публикации в VK, Instagram, Facebook.
"""

import os, sqlite3, logging, json
from datetime import datetime
from typing import Optional
from pathlib import Path
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("musa")
logging.basicConfig(level=logging.INFO, format="[МУСА] %(message)s")

router  = APIRouter(prefix="/content", tags=["musa"])
DB_PATH = str(_DATA_DIR / "musa.db")
OLLAMA_URL   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

Path("data").mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            topic        TEXT NOT NULL,
            text         TEXT NOT NULL,
            platform     TEXT,
            published_at TEXT,
            status       TEXT DEFAULT 'draft',
            created_at   TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit(); conn.close()

init_db()

# ── LLM генерация ─────────────────────────────────────────────────────────────

async def generate_post_text(topic: str, style: str, length: str, language: str) -> str:
    length_map = {"short": "100-150 слов", "medium": "200-300 слов", "long": "400-500 слов"}
    prompt = (
        f"Напиши пост для соцсетей на тему: «{topic}».\n"
        f"Стиль: {style}\n"
        f"Длина: {length_map.get(length, '200 слов')}\n"
        f"Язык: {language}\n"
        "Добавь подходящие эмодзи и хештеги в конце. Без партнёрских ссылок."
    )
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_URL}/api/generate",
                             json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False})
            return r.json().get("response", "").strip()
    except Exception as e:
        return f"[Ollama недоступна: {e}]\n\nТема: {topic}\n#контент #сиен"

# ── Заглушки публикации ───────────────────────────────────────────────────────

def publish_vk(text: str, image_url: Optional[str] = None) -> dict:
    """
    ЗАГЛУШКА — VK API.
    Реальная интеграция:
        import vk_api
        vk = vk_api.VkApi(token=os.environ['VK_TOKEN'])
        vk.method('wall.post', {'owner_id': '-GROUP_ID', 'message': text})
    Получи токен: https://vk.com/dev/access_token
    """
    logger.info(f"[VK STUB] Публикация: {text[:60]}...")
    return {"platform": "vk", "status": "stub_published", "post_id": "vk_fake_001", "note": "Заглушка"}

def publish_instagram(text: str, image_url: Optional[str] = None) -> dict:
    """
    ЗАГЛУШКА — Meta Graph API (Instagram).
    Реальная интеграция:
        import requests
        IG_USER_ID = os.environ['IG_USER_ID']
        TOKEN = os.environ['IG_ACCESS_TOKEN']
        # 1. Создать медиа-контейнер
        r = requests.post(f"https://graph.facebook.com/v18.0/{IG_USER_ID}/media",
                          params={"caption": text, "image_url": image_url, "access_token": TOKEN})
        media_id = r.json()["id"]
        # 2. Опубликовать
        requests.post(f"https://graph.facebook.com/v18.0/{IG_USER_ID}/media_publish",
                      params={"creation_id": media_id, "access_token": TOKEN})
    Документация: https://developers.facebook.com/docs/instagram-api/guides/content-publishing
    """
    logger.info(f"[INSTAGRAM STUB] {text[:60]}...")
    return {"platform": "instagram", "status": "stub_published", "media_id": "ig_fake_001", "note": "Заглушка"}

def publish_facebook(text: str, image_url: Optional[str] = None) -> dict:
    """
    ЗАГЛУШКА — Facebook Graph API.
    Реальная интеграция:
        import requests
        PAGE_ID = os.environ['FB_PAGE_ID']
        TOKEN   = os.environ['FB_PAGE_TOKEN']
        requests.post(f"https://graph.facebook.com/{PAGE_ID}/feed",
                      data={"message": text, "access_token": TOKEN})
    """
    logger.info(f"[FACEBOOK STUB] {text[:60]}...")
    return {"platform": "facebook", "status": "stub_published", "post_id": "fb_fake_001", "note": "Заглушка"}

PUBLISHERS = {"vk": publish_vk, "instagram": publish_instagram, "facebook": publish_facebook}

# ── Модели ────────────────────────────────────────────────────────────────────

class GenerateReq(BaseModel):
    topic: str
    style: str = "информационный"   # информационный | развлекательный | мотивационный | экспертный
    length: str = "medium"          # short | medium | long
    language: str = "ru"
    platforms: list[str] = []       # ["vk", "instagram", "facebook"] — пустой = только генерация
    image_url: Optional[str] = None

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate(req: GenerateReq):
    """Сгенерировать пост и (опционально) опубликовать."""
    text = await generate_post_text(req.topic, req.style, req.length, req.language)

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute("""
        INSERT INTO posts (topic, text, platform, status)
        VALUES (?,?,?,?)
    """, (req.topic, text, ",".join(req.platforms) if req.platforms else None,
          "draft" if not req.platforms else "published"))
    post_id = cur.lastrowid
    conn.commit(); conn.close()

    publications = []
    for platform in req.platforms:
        publisher = PUBLISHERS.get(platform)
        if publisher:
            result = publisher(text, req.image_url)
            publications.append(result)
        else:
            publications.append({"platform": platform, "error": "Неизвестная платформа"})

    logger.info(f"Пост #{post_id} по теме «{req.topic}» | платформы: {req.platforms}")
    return {"post_id": post_id, "text": text, "topic": req.topic, "publications": publications}

@router.post("/publish/{post_id}")
def publish_existing(post_id: int, platforms: list[str]):
    """Опубликовать уже сгенерированный пост."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    post = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, f"Пост #{post_id} не найден")
    conn.execute("UPDATE posts SET status='published', published_at=datetime('now'), platform=? WHERE id=?",
                 (",".join(platforms), post_id))
    conn.commit(); conn.close()
    results = [PUBLISHERS[p](post["text"]) for p in platforms if p in PUBLISHERS]
    return {"status": "published", "post_id": post_id, "results": results}

@router.get("/posts")
def list_posts(limit: int = 20):
    """Список сгенерированных постов."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM posts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return {"posts": [dict(r) for r in rows]}

@router.get("/health")
def health():
    return {"agent": "Муса", "alive": True, "ollama_model": OLLAMA_MODEL}

app = FastAPI(title="Муса — Контент")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8010)
