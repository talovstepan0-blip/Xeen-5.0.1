"""
core/learning.py — Модуль обучения Сиен 3.0.0.

Хранит выученные соответствия «фраза → действие» в data/sien.db.
Три источника предложений:
  1. Rule-based: матч по имени страницы/плагина/агента (fuzzy)
  2. LLM: если Ollama запущена — генерирует 3 варианта
  3. Недавние популярные команды
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

logger = logging.getLogger("learning")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "sien.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS learned_commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase       TEXT NOT NULL,
    normalized   TEXT NOT NULL UNIQUE,
    action_type  TEXT NOT NULL,           -- nav | agent | plugin | custom
    action_data  TEXT NOT NULL,           -- JSON
    confidence   REAL DEFAULT 1.0,
    uses_count   INTEGER DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now')),
    last_used_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_learned_norm ON learned_commands(normalized);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower().strip())


# ══════════════════════════════════════════════════════════════════
# Поиск в обученных командах
# ══════════════════════════════════════════════════════════════════

def find_learned(query: str) -> Optional[dict]:
    """Ищет точное или почти точное совпадение."""
    norm = normalize(query)
    if not norm:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM learned_commands WHERE normalized=?",
            (norm,),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE learned_commands
                   SET uses_count=uses_count+1, last_used_at=datetime('now')
                   WHERE id=?""",
                (row["id"],),
            )
            return dict(row)
    return None


def learn(phrase: str, action_type: str, action_data: dict) -> int:
    """Сохраняет новую обученную команду."""
    norm = normalize(phrase)
    with _db() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO learned_commands
                   (phrase, normalized, action_type, action_data)
                   VALUES (?, ?, ?, ?)""",
                (phrase, norm, action_type,
                 json.dumps(action_data, ensure_ascii=False)),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Уже существует — обновляем
            conn.execute(
                """UPDATE learned_commands
                   SET action_type=?, action_data=?, confidence=1.0,
                       last_used_at=datetime('now')
                   WHERE normalized=?""",
                (action_type, json.dumps(action_data, ensure_ascii=False), norm),
            )
            row = conn.execute(
                "SELECT id FROM learned_commands WHERE normalized=?", (norm,)
            ).fetchone()
            return row["id"] if row else 0


def forget(phrase_or_id) -> bool:
    """Удаляет выученную команду."""
    with _db() as conn:
        if isinstance(phrase_or_id, int):
            cur = conn.execute(
                "DELETE FROM learned_commands WHERE id=?", (phrase_or_id,)
            )
        else:
            cur = conn.execute(
                "DELETE FROM learned_commands WHERE normalized=?",
                (normalize(phrase_or_id),),
            )
        return cur.rowcount > 0


def list_all(limit: int = 100) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            """SELECT * FROM learned_commands
               ORDER BY uses_count DESC, last_used_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def rate_down(phrase: str) -> None:
    """Понижает confidence. Если < 0.3 — удаляет."""
    norm = normalize(phrase)
    with _db() as conn:
        conn.execute(
            """UPDATE learned_commands SET confidence=confidence-0.15
               WHERE normalized=?""",
            (norm,),
        )
        conn.execute(
            "DELETE FROM learned_commands WHERE normalized=? AND confidence<0.3",
            (norm,),
        )


# ══════════════════════════════════════════════════════════════════
# Генерация предложений
# ══════════════════════════════════════════════════════════════════

# Известные nav-пути
NAV_OPTIONS = [
    ("настройки", "Открыть настройки", {"url": "/dashboard/#profile"}),
    ("профиль", "Открыть профиль", {"url": "/dashboard/#profile"}),
    ("дашборд", "Открыть дашборд", {"url": "/dashboard/"}),
    ("агенты", "Список агентов", {"url": "/dashboard/#agents"}),
    ("задачи", "Список задач", {"url": "/dashboard/#tasks"}),
    ("api", "API-ключи", {"url": "/dashboard/#api-keys"}),
    ("ключи", "API-ключи", {"url": "/dashboard/#api-keys"}),
    ("2fa", "Настроить 2FA", {"url": "/dashboard/#security"}),
    ("мониторинг", "Мониторинг CPU/RAM", {"url": "/dashboard/#system"}),
    ("система", "Мониторинг системы", {"url": "/dashboard/#system"}),
    ("диалоги", "История диалогов", {"url": "/dashboard/#dialogs"}),
    ("плагины", "Список плагинов", {"url": "/dashboard/#plugins"}),
    ("обучение", "Обученные команды", {"url": "/dashboard/#learning"}),
    ("llm", "Настройки LLM", {"url": "/dashboard/#llm"}),
    ("ollama", "Настройки Ollama", {"url": "/dashboard/#llm"}),
]

PLUGIN_OPTIONS = [
    ("канбан", "plugins.productivity.kanban", "Канбан-доска"),
    ("kanban", "plugins.productivity.kanban", "Канбан-доска"),
    ("матрица", "plugins.productivity.eisenhower_matrix", "Матрица Эйзенхауэра"),
    ("эйзенхауэр", "plugins.productivity.eisenhower_matrix", "Матрица Эйзенхауэра"),
    ("проекты", "plugins.productivity.project_kanban", "Канбан проектов"),
    ("цели", "plugins.productivity.goals", "Цели"),
    ("привычки", "plugins.productivity.habit_checklist", "Привычки"),
    ("фокус", "plugins.productivity.focus_mode", "Режим фокусировки"),
    ("план", "plugins.productivity.daily_planner", "План дня"),
    ("рутины", "plugins.productivity.routines", "Рутины"),
    ("макросы", "plugins.productivity.macros", "Макросы"),
    ("заметки", "plugins.core.notes", "Заметки"),
    ("погода", "plugins.core.weather", "Погода"),
    ("новости", "plugins.core.news", "Новости"),
]


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def suggest(query: str, max_results: int = 5) -> list[dict]:
    """
    Возвращает список предложений для команды, которую не распознали.
    Формат каждого предложения:
      {"id": "s1", "label": "...", "action": {"type": "nav", ...}, "score": 0.85}
    """
    norm = normalize(query)
    if not norm:
        return []

    candidates: list[tuple[float, dict]] = []

    # 1. Nav-опции
    for keyword, label, data in NAV_OPTIONS:
        score = _similarity(norm, keyword)
        if keyword in norm or norm in keyword or score >= 0.55:
            candidates.append((score + (0.2 if keyword in norm else 0), {
                "label": "🔗 " + label,
                "action": {"type": "nav", **data},
            }))

    # 2. Плагины
    for keyword, module, label in PLUGIN_OPTIONS:
        score = _similarity(norm, keyword)
        if keyword in norm or norm in keyword or score >= 0.55:
            candidates.append((score + (0.2 if keyword in norm else 0), {
                "label": "🔌 " + label,
                "action": {"type": "plugin", "module": module, "label": label},
            }))

    # 3. Популярные обученные команды (fuzzy)
    try:
        with _db() as conn:
            rows = conn.execute(
                """SELECT phrase, action_type, action_data FROM learned_commands
                   ORDER BY uses_count DESC LIMIT 20"""
            ).fetchall()
        for r in rows:
            score = _similarity(norm, normalize(r["phrase"]))
            if score >= 0.5:
                candidates.append((score, {
                    "label": "📚 " + r["phrase"],
                    "action": {
                        "type": r["action_type"],
                        **json.loads(r["action_data"]),
                    },
                }))
    except Exception as e:
        logger.debug(f"learned fetch: {e}")

    # Убираем дубли (по action.url или action.module)
    seen = set()
    unique: list[tuple[float, dict]] = []
    for score, item in candidates:
        key = json.dumps(item["action"], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append((score, item))

    # Сортируем по score
    unique.sort(key=lambda x: -x[0])

    # Формируем результат с id
    result = []
    for i, (score, item) in enumerate(unique[:max_results]):
        result.append({
            "id": f"s{i+1}",
            "label": item["label"],
            "action": item["action"],
            "score": round(score, 2),
        })
    return result


def suggest_with_llm(query: str, max_results: int = 5,
                     ollama_url: str = "http://localhost:11434") -> list[dict]:
    """
    То же что suggest(), но дополнительно спрашивает Ollama.
    Если Ollama недоступна — возвращает только rule-based результаты.
    """
    base = suggest(query, max_results=max_results)

    # Пробуем обогатить через Ollama (синхронно, с таймаутом)
    try:
        import urllib.request
        prompt = (
            f"Пользователь сказал: «{query}». "
            "Предложи 2 варианта что он мог иметь в виду, если это команда компьютерному ассистенту. "
            "Ответь строго JSON-массивом: "
            '[{"label":"...","action":"..."}, {"label":"...","action":"..."}]'
        )
        data = json.dumps({
            "model": "llama3.2:3b",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3},
        }).encode()

        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            resp = json.loads(r.read().decode())
        raw = resp.get("response", "")
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            llm_items = json.loads(m.group())
            for i, it in enumerate(llm_items[:2]):
                base.append({
                    "id": f"llm{i+1}",
                    "label": "🧠 " + str(it.get("label", ""))[:60],
                    "action": {"type": "custom", "text": str(it.get("action", ""))},
                    "score": 0.5,
                })
    except Exception as e:
        logger.debug(f"LLM suggest skip: {e}")

    return base[:max_results]
