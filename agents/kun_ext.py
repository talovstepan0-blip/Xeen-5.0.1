"""
agents/kun_ext.py — Расширения агента Кун (Профессор) для Сиен 3.0.0.

Добавляет к agents/kun.py:
  • Генерация курсов: LLM создаёт структуру (модули → уроки) и контент.
  • Адаптивное обучение: при ошибках LLM упрощает следующее объяснение.
  • Прогресс ученика по урокам.

Интеграция:
    from agents.kun_ext import setup_kun_ext
    setup_kun_ext(app)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import aiohttp
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("kun.ext")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "kun_ext.db"

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:3b"

router = APIRouter(prefix="/professor", tags=["kun-ext"])

# ══════════════════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    level       TEXT,             -- beginner | intermediate | advanced
    structure   TEXT NOT NULL,    -- JSON: {modules: [...]}
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lessons (
    id          TEXT PRIMARY KEY,
    course_id   TEXT NOT NULL,
    module_idx  INTEGER NOT NULL,
    lesson_idx  INTEGER NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT,
    difficulty  INTEGER DEFAULT 3,   -- 1=просто .. 5=сложно
    FOREIGN KEY (course_id) REFERENCES courses(id)
);
CREATE INDEX IF NOT EXISTS idx_lessons_course ON lessons(course_id);

CREATE TABLE IF NOT EXISTS progress (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   TEXT NOT NULL,
    lesson_id   TEXT NOT NULL,
    completed   INTEGER DEFAULT 0,
    correct     INTEGER DEFAULT 0,
    incorrect   INTEGER DEFAULT 0,
    last_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(course_id, lesson_id)
);

CREATE TABLE IF NOT EXISTS quiz_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   TEXT NOT NULL,
    lesson_id   TEXT NOT NULL,
    question    TEXT NOT NULL,
    user_answer TEXT,
    correct     INTEGER NOT NULL,
    asked_at    TEXT DEFAULT (datetime('now'))
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
# LLM
# ══════════════════════════════════════════════════════════════════

async def call_llm(prompt: str, temperature: float = 0.4) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": temperature}},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("response", "")
    except Exception as e:
        logger.warning(f"LLM: {e}")
    return ""


def extract_json(text: str) -> dict:
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {}


# ══════════════════════════════════════════════════════════════════
# Генерация курса
# ══════════════════════════════════════════════════════════════════

class CourseGenerateRequest(BaseModel):
    topic: str
    level: str = "beginner"   # beginner | intermediate | advanced
    modules_count: int = 5
    lessons_per_module: int = 3


@router.post("/courses/generate")
async def generate_course(req: CourseGenerateRequest):
    """LLM создаёт структуру курса (модули → уроки)."""
    prompt = (
        f"Создай структуру учебного курса по теме «{req.topic}» уровень {req.level}.\n"
        f"Должно быть {req.modules_count} модулей, по {req.lessons_per_module} уроков в каждом.\n"
        "Верни СТРОГО JSON:\n"
        '{"title": "...", "description": "...", '
        '"modules": [{"title": "...", "lessons": [{"title": "..."}]}]}\n'
        "JSON:"
    )
    raw = await call_llm(prompt, temperature=0.5)
    structure = extract_json(raw)
    if not structure or "modules" not in structure:
        raise HTTPException(500, "LLM не вернула валидную структуру курса")

    course_id = str(uuid4())[:10]
    with get_db() as conn:
        conn.execute(
            """INSERT INTO courses (id, title, description, level, structure)
               VALUES (?, ?, ?, ?, ?)""",
            (course_id,
             structure.get("title", req.topic),
             structure.get("description", ""),
             req.level,
             json.dumps(structure, ensure_ascii=False)),
        )

        # Создаём заглушки уроков (контент будет генериться по запросу)
        for m_idx, module in enumerate(structure.get("modules", [])):
            for l_idx, lesson in enumerate(module.get("lessons", [])):
                lid = f"{course_id}_{m_idx}_{l_idx}"
                conn.execute(
                    """INSERT INTO lessons
                       (id, course_id, module_idx, lesson_idx, title, content)
                       VALUES (?, ?, ?, ?, ?, NULL)""",
                    (lid, course_id, m_idx, l_idx, lesson.get("title", "Урок")),
                )

    return {"course_id": course_id, "structure": structure}


@router.get("/courses/list")
def list_courses():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, description, level, created_at FROM courses ORDER BY created_at DESC"
        ).fetchall()
    return {"courses": [dict(r) for r in rows], "count": len(rows)}


@router.get("/courses/{course_id}")
def get_course(course_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Курс не найден")
        lessons = conn.execute(
            """SELECT id, module_idx, lesson_idx, title, difficulty
               FROM lessons WHERE course_id=? ORDER BY module_idx, lesson_idx""",
            (course_id,),
        ).fetchall()
    course = dict(row)
    course["structure"] = json.loads(course["structure"])
    course["lessons"] = [dict(l) for l in lessons]
    return course


# ══════════════════════════════════════════════════════════════════
# Адаптивные уроки (LLM упрощает при ошибках)
# ══════════════════════════════════════════════════════════════════

@router.get("/courses/{course_id}/lessons/{lesson_id}")
async def get_lesson(course_id: str, lesson_id: str):
    """Возвращает контент урока. Если контента нет — генерирует его."""
    with get_db() as conn:
        lesson = conn.execute(
            "SELECT * FROM lessons WHERE id=? AND course_id=?",
            (lesson_id, course_id),
        ).fetchone()
        if not lesson:
            raise HTTPException(404, "Урок не найден")
        course = conn.execute(
            "SELECT title, level FROM courses WHERE id=?", (course_id,)
        ).fetchone()

        # Считаем процент ошибок ученика по этому курсу
        progress = conn.execute(
            """SELECT SUM(correct) AS c, SUM(incorrect) AS i
               FROM progress WHERE course_id=?""",
            (course_id,),
        ).fetchone()

    error_rate = 0.0
    total = (progress["c"] or 0) + (progress["i"] or 0)
    if total > 0:
        error_rate = (progress["i"] or 0) / total

    lesson_d = dict(lesson)
    if not lesson_d.get("content"):
        # Адаптация: если ученик часто ошибается — просим LLM упростить
        difficulty_hint = ""
        if error_rate > 0.4:
            difficulty_hint = " Объясняй максимально просто, для новичка."
        elif error_rate < 0.1 and total > 5:
            difficulty_hint = " Можно использовать сложные термины."

        prompt = (
            f"Напиши учебный материал для урока «{lesson_d['title']}» "
            f"в курсе «{dict(course)['title']}» уровень {dict(course)['level']}.{difficulty_hint}\n"
            "Дай 3-5 абзацев с примерами. Не используй markdown заголовки уровня 1."
        )
        content = await call_llm(prompt, temperature=0.6)

        # Сохраним
        with get_db() as conn:
            conn.execute(
                "UPDATE lessons SET content=? WHERE id=?",
                (content, lesson_id),
            )
        lesson_d["content"] = content
        lesson_d["adaptive_hint"] = difficulty_hint or "стандартная подача"

    return lesson_d


# ══════════════════════════════════════════════════════════════════
# Прогресс и квизы
# ══════════════════════════════════════════════════════════════════

class QuizAnswer(BaseModel):
    course_id: str
    lesson_id: str
    question: str
    user_answer: str
    correct: bool


@router.post("/quiz/answer")
def submit_answer(req: QuizAnswer):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO quiz_log (course_id, lesson_id, question, user_answer, correct)
               VALUES (?, ?, ?, ?, ?)""",
            (req.course_id, req.lesson_id, req.question,
             req.user_answer, int(req.correct)),
        )
        conn.execute(
            """INSERT INTO progress (course_id, lesson_id, correct, incorrect)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(course_id, lesson_id) DO UPDATE SET
                 correct=correct+excluded.correct,
                 incorrect=incorrect+excluded.incorrect,
                 last_at=datetime('now')""",
            (req.course_id, req.lesson_id,
             int(req.correct), int(not req.correct)),
        )
    return {"status": "logged", "correct": req.correct}


@router.post("/lessons/{lesson_id}/complete")
def mark_lesson_complete(lesson_id: str):
    with get_db() as conn:
        lesson = conn.execute(
            "SELECT course_id FROM lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        if not lesson:
            raise HTTPException(404, "Урок не найден")
        conn.execute(
            """INSERT INTO progress (course_id, lesson_id, completed)
               VALUES (?, ?, 1)
               ON CONFLICT(course_id, lesson_id) DO UPDATE SET
                 completed=1, last_at=datetime('now')""",
            (lesson["course_id"], lesson_id),
        )
    return {"status": "completed"}


@router.get("/courses/{course_id}/progress")
def course_progress(course_id: str):
    with get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE course_id=?", (course_id,)
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM progress WHERE course_id=? AND completed=1",
            (course_id,),
        ).fetchone()[0]
        stats = conn.execute(
            """SELECT SUM(correct) AS correct, SUM(incorrect) AS incorrect
               FROM progress WHERE course_id=?""",
            (course_id,),
        ).fetchone()
    return {
        "course_id": course_id,
        "total_lessons": total,
        "completed_lessons": completed,
        "progress_pct": round(completed / total * 100, 1) if total else 0,
        "correct_answers": stats["correct"] or 0,
        "incorrect_answers": stats["incorrect"] or 0,
    }


# ══════════════════════════════════════════════════════════════════
# Setup
# ══════════════════════════════════════════════════════════════════

def setup_kun_ext(app: FastAPI) -> None:
    init_db()
    app.include_router(router)
    logger.info("Расширения Куна зарегистрированы")
