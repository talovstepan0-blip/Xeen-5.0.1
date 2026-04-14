"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Каллио' (Медиум) — рекомендации фильмов, книг, игр.
Этап 4 проекта 'Сиен 01'.

SQLite для истории предпочтений + LLM рекомендации + заглушки Kinopoisk/IMDb API.
"""

import os, sqlite3, logging, json
from typing import Optional
from pathlib import Path
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("kallio")
logging.basicConfig(level=logging.INFO, format="[КАЛЛИО] %(message)s")

router  = APIRouter(prefix="/medium", tags=["kallio"])
DB_PATH = str(_DATA_DIR / "kallio.db")
OLLAMA_URL   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

Path("data").mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS preferences (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type TEXT NOT NULL,     -- film | book | game
            title      TEXT NOT NULL,
            rating     INTEGER,           -- 1-10 (лайк=8, дизлайк=2)
            action     TEXT NOT NULL,     -- like | dislike | watched | reading | wishlist
            genres     TEXT DEFAULT '[]', -- JSON
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS recommendations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type TEXT,
            items      TEXT NOT NULL,    -- JSON
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit(); conn.close()

init_db()

# ── Внешние API (заглушки) ────────────────────────────────────────────────────

def search_kinopoisk(query: str) -> list[dict]:
    """
    ЗАГЛУШКА — Kinopoisk Unofficial API.
    Реальная интеграция:
        import requests
        r = requests.get("https://kinopoiskapiunofficial.tech/api/v2.1/films/search-by-keyword",
                         headers={"X-API-KEY": os.environ['KINOPOISK_TOKEN']},
                         params={"keyword": query})
        return r.json()["films"]
    Получи токен: https://kinopoiskapiunofficial.tech
    """
    return [{"title": f"[Кинопоиск заглушка] {query}", "year": 2024, "rating": 7.5}]

def search_imdb(query: str) -> list[dict]:
    """
    ЗАГЛУШКА — IMDb API (RapidAPI).
    Реальная интеграция:
        import requests
        r = requests.get("https://imdb8.p.rapidapi.com/title/find",
                         headers={"X-RapidAPI-Key": os.environ['RAPIDAPI_KEY']},
                         params={"q": query})
        return r.json().get("results", [])
    """
    return [{"title": f"[IMDb заглушка] {query}", "year": 2024, "rating": 7.2}]

def search_rawg(query: str) -> list[dict]:
    """
    ЗАГЛУШКА — RAWG.io Games API (бесплатный ключ).
    Реальная интеграция:
        import requests
        r = requests.get("https://api.rawg.io/api/games",
                         params={"key": os.environ['RAWG_API_KEY'], "search": query})
        return r.json().get("results", [])
    Получи ключ: https://rawg.io/apidocs
    """
    return [{"title": f"[RAWG заглушка] {query}", "rating": 4.2, "genres": ["action"]}]

async def llm_recommend(media_type: str, history: list[dict], preferences: str) -> str:
    liked    = [h["title"] for h in history if h["action"] == "like"]
    disliked = [h["title"] for h in history if h["action"] == "dislike"]
    prompt   = (
        f"Ты эксперт по {media_type}. Порекомендуй 5 {media_type} на русском языке.\n"
        f"Понравилось: {', '.join(liked) or 'не указано'}\n"
        f"Не понравилось: {', '.join(disliked) or 'не указано'}\n"
        f"Дополнительные предпочтения: {preferences or 'не указаны'}\n\n"
        "Формат: нумерованный список с кратким обоснованием каждой рекомендации."
    )
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_URL}/api/generate",
                             json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False})
            return r.json().get("response", "").strip()
    except Exception as e:
        return f"[Ollama недоступна: {e}]"

# ── Модели ────────────────────────────────────────────────────────────────────

class LikeReq(BaseModel):
    media_type: str       # film | book | game
    title: str
    action: str = "like"  # like | dislike | watched | reading | wishlist
    rating: Optional[int] = None
    genres: list[str] = []

class RecommendReq(BaseModel):
    media_type: str = "film"
    preferences: Optional[str] = None
    limit: int = 5

class SearchReq(BaseModel):
    query: str
    media_type: str = "film"  # film | game

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/like")
def like(req: LikeReq):
    """Добавить оценку контента (лайк/дизлайк/просмотрено)."""
    rating = req.rating or (8 if req.action == "like" else 2 if req.action == "dislike" else 5)
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute("""
        INSERT INTO preferences (media_type, title, rating, action, genres)
        VALUES (?,?,?,?,?)
    """, (req.media_type, req.title, rating, req.action, json.dumps(req.genres)))
    pref_id = cur.lastrowid
    conn.commit(); conn.close()
    logger.info(f"Записано: {req.action} — «{req.title}» ({req.media_type})")
    return {"status": "saved", "id": pref_id, "title": req.title, "action": req.action}

@router.post("/recommend")
async def recommend(req: RecommendReq):
    """LLM-рекомендации на основе истории предпочтений."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    history = conn.execute(
        "SELECT * FROM preferences WHERE media_type=? ORDER BY created_at DESC LIMIT 30",
        (req.media_type,)
    ).fetchall()
    conn.close()

    advice = await llm_recommend(req.media_type, [dict(h) for h in history], req.preferences or "")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO recommendations (media_type, items) VALUES (?,?)",
                 (req.media_type, advice))
    conn.commit(); conn.close()

    return {
        "media_type": req.media_type,
        "recommendations": advice,
        "based_on": len(history),
    }

@router.post("/search")
def search(req: SearchReq):
    """Поиск по внешним API (Kinopoisk/RAWG)."""
    if req.media_type == "game":
        results = search_rawg(req.query)
    else:
        results = search_kinopoisk(req.query)
    return {"query": req.query, "results": results, "note": "Используются заглушки API"}

@router.get("/history")
def history(media_type: Optional[str] = None, limit: int = 50):
    """История предпочтений."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if media_type:
        rows = conn.execute(
            "SELECT * FROM preferences WHERE media_type=? ORDER BY created_at DESC LIMIT ?",
            (media_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM preferences ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return {"history": [dict(r) for r in rows], "total": len(rows)}

@router.get("/health")
def health():
    return {"agent": "Каллио", "alive": True}

app = FastAPI(title="Каллио — Медиум")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8011)
