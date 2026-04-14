"""
Агент 'Мастер' (Тренер) — здоровье и фитнес.
Этап 4 проекта 'Сиен 01'.

Интеграции: Garmin/Strava (заглушки), ручной ввод активности, LLM-план тренировок.
"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

import os, sqlite3, logging, json
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("master")
logging.basicConfig(level=logging.INFO, format="[МАСТЕР] %(message)s")

router   = APIRouter(prefix="/trainer", tags=["master"])
DB_PATH  = str(_DATA_DIR / "master.db")
OLLAMA_URL   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

Path("data").mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS activities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL,
            duration_min REAL,
            distance_km  REAL,
            calories     REAL,
            heart_rate   REAL,
            notes        TEXT,
            recorded_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS plans (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_json  TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            goal       TEXT
        );
    """)
    conn.commit(); conn.close()

init_db()

# ── Заглушки внешних API ──────────────────────────────────────────────────────

def fetch_garmin_stub(days: int = 7) -> list[dict]:
    """
    ЗАГЛУШКА — Garmin Connect IQ API.
    Реальная интеграция:
        from garminconnect import Garmin
        api = Garmin(email, password); api.login()
        activities = api.get_activities(0, days)
    Установи: pip install garminconnect
    """
    logger.warning("Garmin: используется заглушка")
    return [{"type": "running", "duration_min": 30, "distance_km": 5.2, "heart_rate": 145, "source": "garmin_stub"}]

def fetch_strava_stub(days: int = 7) -> list[dict]:
    """
    ЗАГЛУШКА — Strava API v3.
    Реальная интеграция:
        import stravalib
        client = stravalib.Client(access_token=os.environ['STRAVA_TOKEN'])
        activities = client.get_activities(after=datetime.now()-timedelta(days=days))
    Установи: pip install stravalib
    Получи токен: https://www.strava.com/settings/api
    """
    logger.warning("Strava: используется заглушка")
    return [{"type": "cycling", "duration_min": 45, "distance_km": 15.0, "heart_rate": 130, "source": "strava_stub"}]

async def llm_generate(prompt: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_URL}/api/generate",
                             json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False})
            return r.json().get("response", "").strip()
    except Exception as e:
        return f"[Ollama недоступна: {e}. Запусти: ollama serve]"

# ── Модели ────────────────────────────────────────────────────────────────────

class ActivityLog(BaseModel):
    type: str                     # running | cycling | swimming | gym | yoga | other
    duration_min: float
    distance_km: Optional[float]  = None
    calories: Optional[float]     = None
    heart_rate: Optional[float]   = None
    notes: Optional[str]          = None

class PlanRequest(BaseModel):
    goal: str                     # "похудеть", "набрать мышцы", "марафон"
    days_per_week: int = 4
    duration_weeks: int = 4
    current_level: str = "beginner"  # beginner | intermediate | advanced

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/log_activity")
def log_activity(act: ActivityLog):
    """Записать активность вручную."""
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute("""
        INSERT INTO activities (type, duration_min, distance_km, calories, heart_rate, notes)
        VALUES (?,?,?,?,?,?)
    """, (act.type, act.duration_min, act.distance_km, act.calories, act.heart_rate, act.notes))
    activity_id = cur.lastrowid
    conn.commit(); conn.close()
    logger.info(f"Активность #{activity_id} записана: {act.type} {act.duration_min}мин")
    return {"status": "logged", "id": activity_id, "type": act.type}

@router.get("/stats")
def get_stats(days: int = 30):
    """Статистика за последние N дней + рекомендации."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows  = conn.execute(
        "SELECT * FROM activities WHERE recorded_at >= ? ORDER BY recorded_at DESC", (since,)
    ).fetchall()
    conn.close()

    if not rows:
        return {"message": f"Нет активностей за последние {days} дней", "activities": []}

    total_min = sum(r["duration_min"] for r in rows)
    total_km  = sum(r["distance_km"] or 0 for r in rows)
    avg_hr    = sum(r["heart_rate"] or 0 for r in rows) / max(len(rows), 1)
    by_type   = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1

    # Простые рекомендации
    recommendations = []
    if total_min / days < 30:
        recommendations.append("Старайся тренироваться минимум 30 минут в день")
    if avg_hr > 160:
        recommendations.append("Средний пульс высокий — добавь восстановительные тренировки")
    if "running" not in by_type and "cycling" not in by_type:
        recommendations.append("Добавь кардио-нагрузку для улучшения выносливости")

    return {
        "period_days": days,
        "total_sessions": len(rows),
        "total_minutes": round(total_min, 1),
        "total_km": round(total_km, 1),
        "avg_heart_rate": round(avg_hr, 1),
        "by_type": by_type,
        "recommendations": recommendations,
        "recent": [dict(r) for r in rows[:5]],
    }

@router.post("/plan")
async def generate_plan(req: PlanRequest):
    """Сгенерировать план тренировок на неделю через LLM."""
    prompt = (
        f"Составь детальный план тренировок на {req.duration_weeks} недели.\n"
        f"Цель: {req.goal}\n"
        f"Уровень: {req.current_level}\n"
        f"Тренировочных дней в неделю: {req.days_per_week}\n\n"
        "Укажи для каждой тренировки: тип, продолжительность, интенсивность, упражнения. "
        "Формат: по неделям и дням недели. Язык: русский."
    )
    plan_text = await llm_generate(prompt)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO plans (plan_json, goal) VALUES (?,?)", (plan_text, req.goal))
    conn.commit(); conn.close()
    return {"goal": req.goal, "plan": plan_text, "weeks": req.duration_weeks}

@router.post("/sync/garmin")
def sync_garmin():
    """Синхронизировать данные с Garmin (заглушка)."""
    activities = fetch_garmin_stub()
    conn = sqlite3.connect(DB_PATH)
    for a in activities:
        conn.execute("""
            INSERT INTO activities (type, duration_min, distance_km, heart_rate, notes)
            VALUES (?,?,?,?,?)
        """, (a["type"], a["duration_min"], a.get("distance_km"), a.get("heart_rate"), a.get("source")))
    conn.commit(); conn.close()
    return {"status": "synced", "source": "garmin", "count": len(activities), "note": "Заглушка"}

@router.post("/sync/strava")
def sync_strava():
    """Синхронизировать данные со Strava (заглушка)."""
    activities = fetch_strava_stub()
    conn = sqlite3.connect(DB_PATH)
    for a in activities:
        conn.execute("""
            INSERT INTO activities (type, duration_min, distance_km, heart_rate, notes)
            VALUES (?,?,?,?,?)
        """, (a["type"], a["duration_min"], a.get("distance_km"), a.get("heart_rate"), a.get("source")))
    conn.commit(); conn.close()
    return {"status": "synced", "source": "strava", "count": len(activities), "note": "Заглушка"}

@router.get("/health")
def health():
    return {"agent": "Мастер", "alive": True}

app = FastAPI(title="Мастер — Тренер")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8008)

# ── Сиен 3.0.0: подключаем расширения ──
try:
    from agents.master_ext import setup_master_ext
    setup_master_ext(app)
except Exception as _ext_err:
    import logging as _l
    _l.getLogger('master').warning(f'Расширения недоступны: {_ext_err}')
