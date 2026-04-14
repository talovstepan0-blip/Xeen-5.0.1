"""
agents/master_ext.py — Расширения агента Мастер (Тренер) для Сиен 3.0.0.

Добавляет к существующему agents/master.py:
  • Анализ сна (импорт CSV из Mi Band / Garmin / Apple Health)
  • Дневник питания с LLM-анализом калорийности
  • Голосовые подсказки во время тренировки (через TTS-агент Эхо)

Интеграция:
  В конце agents/master.py добавь:
      from agents.master_ext import setup_master_ext
      setup_master_ext(app)
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import APIRouter, FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel

logger = logging.getLogger("master.ext")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "master_ext.db"

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:3b"
EHO_URL = "http://localhost:8016"

router = APIRouter(prefix="/trainer", tags=["master-ext"])


# ══════════════════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS sleep_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    bedtime     TEXT,
    wake_time   TEXT,
    duration_h  REAL,
    deep_h      REAL,
    rem_h       REAL,
    light_h     REAL,
    quality     INTEGER,        -- 0..100
    source      TEXT NOT NULL,  -- mi_band | garmin | apple | manual
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sleep_date ON sleep_records(date DESC);

CREATE TABLE IF NOT EXISTS nutrition_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    meal        TEXT NOT NULL,    -- breakfast | lunch | dinner | snack
    food        TEXT NOT NULL,
    calories    REAL,
    protein_g   REAL,
    carbs_g     REAL,
    fat_g       REAL,
    llm_analysis TEXT,            -- JSON анализа от LLM
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_nutrition_date ON nutrition_log(date DESC);

CREATE TABLE IF NOT EXISTS workout_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    type        TEXT,
    plan        TEXT,             -- JSON список упражнений
    notes       TEXT
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


# ══════════════════════════════════════════════════════════════════
# СОН
# ══════════════════════════════════════════════════════════════════

class SleepRecord(BaseModel):
    date: str
    bedtime: Optional[str] = None
    wake_time: Optional[str] = None
    duration_h: Optional[float] = None
    quality: Optional[int] = None
    source: str = "manual"
    notes: Optional[str] = None


def parse_mi_band_csv(content: str) -> list[dict]:
    """
    Парсит экспорт Mi Fit / Zepp Life.
    Ожидаемые колонки: date, deepSleepTime, shallowSleepTime, start, stop
    """
    records: list[dict] = []
    try:
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            try:
                deep = float(row.get("deepSleepTime", 0)) / 60   # минуты → часы
                light = float(row.get("shallowSleepTime", 0)) / 60
                duration = deep + light
                records.append({
                    "date": row.get("date", "")[:10],
                    "bedtime": row.get("start"),
                    "wake_time": row.get("stop"),
                    "duration_h": round(duration, 2),
                    "deep_h": round(deep, 2),
                    "light_h": round(light, 2),
                    "source": "mi_band",
                })
            except (ValueError, TypeError):
                continue
    except Exception as e:
        logger.warning(f"Mi Band CSV: {e}")
    return records


def parse_garmin_csv(content: str) -> list[dict]:
    """Парсит экспорт Garmin Connect: date, totalSleepHours, deepSleepHours, ..."""
    records: list[dict] = []
    try:
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            try:
                records.append({
                    "date": (row.get("date") or row.get("Date") or "")[:10],
                    "duration_h": float(row.get("totalSleepHours", row.get("Total Sleep", 0)) or 0),
                    "deep_h": float(row.get("deepSleepHours", row.get("Deep", 0)) or 0),
                    "rem_h": float(row.get("remSleepHours", row.get("REM", 0)) or 0),
                    "light_h": float(row.get("lightSleepHours", row.get("Light", 0)) or 0),
                    "source": "garmin",
                })
            except (ValueError, TypeError):
                continue
    except Exception as e:
        logger.warning(f"Garmin CSV: {e}")
    return records


@router.post("/sleep/import_csv")
async def import_sleep_csv(source: str, file: UploadFile = File(...)):
    """Импорт CSV экспорта Mi Band / Garmin / Apple Health."""
    content = (await file.read()).decode("utf-8", errors="replace")
    if source == "mi_band":
        records = parse_mi_band_csv(content)
    elif source == "garmin":
        records = parse_garmin_csv(content)
    else:
        raise HTTPException(400, f"Источник {source} не поддерживается")

    with get_db() as conn:
        for r in records:
            conn.execute(
                """INSERT INTO sleep_records
                   (date, bedtime, wake_time, duration_h, deep_h, light_h, rem_h, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.get("date"), r.get("bedtime"), r.get("wake_time"),
                 r.get("duration_h"), r.get("deep_h"), r.get("light_h"),
                 r.get("rem_h"), r.get("source")),
            )
    return {"imported": len(records), "source": source}


@router.post("/sleep/manual")
async def add_sleep_manual(rec: SleepRecord):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO sleep_records
               (date, bedtime, wake_time, duration_h, quality, source, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rec.date, rec.bedtime, rec.wake_time, rec.duration_h,
             rec.quality, rec.source, rec.notes),
        )
    return {"status": "added"}


@router.get("/sleep/list")
def list_sleep(limit: int = 30):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sleep_records ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"records": [dict(r) for r in rows], "count": len(rows)}


@router.get("/sleep/analyze")
async def analyze_sleep():
    """LLM-анализ последних 7 дней сна с рекомендациями."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sleep_records ORDER BY date DESC LIMIT 7"
        ).fetchall()

    if not rows:
        return {"error": "Нет данных о сне"}

    records = [dict(r) for r in rows]
    avg_duration = sum(r.get("duration_h", 0) or 0 for r in records) / len(records)
    avg_deep = sum(r.get("deep_h", 0) or 0 for r in records) / len(records)

    prompt = (
        "Проанализируй данные о сне за последние 7 дней и дай 3-5 коротких "
        "рекомендаций для улучшения. Будь конкретным.\n\n"
        f"Средняя длительность: {avg_duration:.1f} ч\n"
        f"Средний глубокий сон: {avg_deep:.1f} ч\n"
        f"Записей: {len(records)}\n\n"
        f"Данные: {json.dumps(records[:7], ensure_ascii=False)}"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.4}},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return {
                        "avg_duration_h": round(avg_duration, 1),
                        "avg_deep_h": round(avg_deep, 1),
                        "records_analyzed": len(records),
                        "recommendations": data.get("response", "").strip(),
                    }
    except Exception as e:
        return {"error": f"LLM недоступна: {e}",
                "avg_duration_h": round(avg_duration, 1),
                "avg_deep_h": round(avg_deep, 1)}


# ══════════════════════════════════════════════════════════════════
# ПИТАНИЕ
# ══════════════════════════════════════════════════════════════════

class NutritionEntry(BaseModel):
    date: Optional[str] = None
    meal: str = "snack"   # breakfast | lunch | dinner | snack
    food: str
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None


async def llm_estimate_nutrition(food: str) -> dict:
    """Спрашивает у LLM приблизительные калории и БЖУ."""
    prompt = (
        "Оцени калорийность и БЖУ для блюда. Ответь СТРОГО JSON:\n"
        '{"calories": число_ккал, "protein_g": г, "carbs_g": г, "fat_g": г, '
        '"comment": "короткий комментарий"}\n\n'
        f"Блюдо: {food}\n\nJSON:"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.3}},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    raw = data.get("response", "")
                    import re
                    m = re.search(r"\{.*\}", raw, re.DOTALL)
                    if m:
                        return json.loads(m.group())
    except Exception as e:
        logger.warning(f"LLM nutrition: {e}")
    return {}


@router.post("/nutrition/add")
async def add_nutrition(entry: NutritionEntry):
    date = entry.date or datetime.now().date().isoformat()
    analysis = {}

    # Если калории не указаны — спросим у LLM
    if entry.calories is None:
        analysis = await llm_estimate_nutrition(entry.food)
        entry.calories = analysis.get("calories")
        entry.protein_g = entry.protein_g or analysis.get("protein_g")
        entry.carbs_g = entry.carbs_g or analysis.get("carbs_g")
        entry.fat_g = entry.fat_g or analysis.get("fat_g")

    with get_db() as conn:
        conn.execute(
            """INSERT INTO nutrition_log
               (date, meal, food, calories, protein_g, carbs_g, fat_g, llm_analysis)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (date, entry.meal, entry.food, entry.calories,
             entry.protein_g, entry.carbs_g, entry.fat_g,
             json.dumps(analysis, ensure_ascii=False) if analysis else None),
        )

    return {
        "status": "added",
        "calories": entry.calories,
        "llm_analysis": analysis,
    }


@router.get("/nutrition/today")
def nutrition_today():
    today = datetime.now().date().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM nutrition_log WHERE date=? ORDER BY id",
            (today,),
        ).fetchall()
    items = [dict(r) for r in rows]
    total_cal = sum(i.get("calories") or 0 for i in items)
    total_p = sum(i.get("protein_g") or 0 for i in items)
    total_c = sum(i.get("carbs_g") or 0 for i in items)
    total_f = sum(i.get("fat_g") or 0 for i in items)
    return {
        "date": today,
        "items": items,
        "total_calories": round(total_cal, 1),
        "total_protein_g": round(total_p, 1),
        "total_carbs_g": round(total_c, 1),
        "total_fat_g": round(total_f, 1),
    }


@router.get("/nutrition/week")
def nutrition_week():
    week_ago = (datetime.now().date() - timedelta(days=7)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT date, SUM(calories) AS cal, COUNT(*) AS items
               FROM nutrition_log WHERE date>=? GROUP BY date ORDER BY date DESC""",
            (week_ago,),
        ).fetchall()
    return {"days": [dict(r) for r in rows]}


# ══════════════════════════════════════════════════════════════════
# ГОЛОСОВЫЕ ПОДСКАЗКИ ВО ВРЕМЯ ТРЕНИРОВКИ
# ══════════════════════════════════════════════════════════════════

class WorkoutStart(BaseModel):
    type: str = "general"
    plan: list[dict]   # [{"name": "Отжимания", "reps": 15, "rest_sec": 60}, ...]


async def speak_via_eho(text: str) -> bool:
    """Отправляет текст агенту Эхо для озвучки."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{EHO_URL}/tts/generate",
                json={"text": text},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return r.status == 200
    except Exception as e:
        logger.debug(f"Эхо: {e}")
        return False


_active_workout: dict = {}


@router.post("/workout/start")
async def workout_start(req: WorkoutStart):
    """Запускает тренировку с голосовыми подсказками."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO workout_sessions (started_at, type, plan)
               VALUES (?, ?, ?)""",
            (datetime.now().isoformat(), req.type,
             json.dumps(req.plan, ensure_ascii=False)),
        )
        wid = cur.lastrowid

    _active_workout[wid] = {"plan": req.plan, "current": 0}

    asyncio.create_task(_workout_loop(wid))
    return {"workout_id": wid, "exercises": len(req.plan), "status": "started"}


async def _workout_loop(wid: int):
    """Идёт по плану и озвучивает каждое упражнение и отдых."""
    state = _active_workout.get(wid)
    if not state:
        return

    await speak_via_eho("Тренировка началась. Готов?")
    await asyncio.sleep(3)

    for idx, ex in enumerate(state["plan"]):
        if wid not in _active_workout:
            return  # отменена
        state["current"] = idx
        name = ex.get("name", "Упражнение")
        reps = ex.get("reps", 10)
        await speak_via_eho(f"Упражнение {idx + 1}. {name}, {reps} повторений.")
        await asyncio.sleep(max(int(reps) * 2, 20))

        rest = int(ex.get("rest_sec", 30))
        if rest > 0 and idx < len(state["plan"]) - 1:
            await speak_via_eho(f"Отдых {rest} секунд.")
            await asyncio.sleep(rest)

    await speak_via_eho("Тренировка завершена! Молодец!")
    with get_db() as conn:
        conn.execute(
            "UPDATE workout_sessions SET finished_at=? WHERE id=?",
            (datetime.now().isoformat(), wid),
        )
    _active_workout.pop(wid, None)


@router.post("/workout/stop/{wid}")
def workout_stop(wid: int):
    if wid in _active_workout:
        _active_workout.pop(wid, None)
        with get_db() as conn:
            conn.execute(
                "UPDATE workout_sessions SET finished_at=? WHERE id=?",
                (datetime.now().isoformat(), wid),
            )
        return {"status": "stopped", "workout_id": wid}
    raise HTTPException(404, "Тренировка не найдена")


@router.get("/workout/active")
def workout_active():
    return {"active": list(_active_workout.keys()), "count": len(_active_workout)}


# ══════════════════════════════════════════════════════════════════
# Setup
# ══════════════════════════════════════════════════════════════════

def setup_master_ext(app: FastAPI) -> None:
    init_db()
    app.include_router(router)
    logger.info("Расширения Мастера зарегистрированы")
