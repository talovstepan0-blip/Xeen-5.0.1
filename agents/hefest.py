"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Гефест' (Генерация кода) — написание и отладка кода через Ollama.
Этап 4 проекта 'Сиен 01'.

Модель по умолчанию: deepseek-coder (или любая через env CODEGEN_MODEL).
Поддерживает итеративную отладку: передаёт ошибку обратно в LLM.
"""

import os, sqlite3, logging, re
from datetime import datetime
from typing import Optional
from pathlib import Path
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("hefest")
logging.basicConfig(level=logging.INFO, format="[ГЕФЕСТ] %(message)s")

router  = APIRouter(prefix="/codegen", tags=["hefest"])
DB_PATH = str(_DATA_DIR / "hefest.db")
OLLAMA_URL    = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CODEGEN_MODEL = os.environ.get("CODEGEN_MODEL", "deepseek-coder")  # или llama3.2:3b

Path("data").mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS generations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            language    TEXT NOT NULL,
            description TEXT NOT NULL,
            code        TEXT NOT NULL,
            model       TEXT,
            iterations  INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS debug_sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            generation_id INTEGER,
            error_text    TEXT,
            fixed_code    TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit(); conn.close()

init_db()

# ── LLM вызовы ────────────────────────────────────────────────────────────────

async def call_codegen(prompt: str, model: str = None) -> str:
    """Вызов кодогенерирующей LLM через Ollama."""
    m = model or CODEGEN_MODEL
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": m,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 2048}
                }
            )
            r.raise_for_status()
            return r.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ollama ошибка: {e}")
        raise HTTPException(503, f"Ollama недоступна: {e}. Запусти: ollama serve && ollama pull {m}")

def extract_code(raw: str, language: str) -> str:
    """Извлечь код из ответа LLM (убрать markdown-блоки)."""
    pattern = rf"```(?:{language}|python|js|javascript|ts|typescript|bash|sh)?\n?(.*?)```"
    matches = re.findall(pattern, raw, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[0].strip()
    # Если нет блоков — вернуть как есть
    return raw.strip()

# ── Модели ────────────────────────────────────────────────────────────────────

class GenerateReq(BaseModel):
    language: str              # python | javascript | typescript | bash | go | rust | sql | other
    description: str           # описание задачи
    context: Optional[str] = None  # доп. контекст / существующий код
    model: Optional[str] = None    # переопределить модель

class DebugReq(BaseModel):
    code: str
    error: str
    language: str
    generation_id: Optional[int] = None
    model: Optional[str] = None

class ReviewReq(BaseModel):
    code: str
    language: str
    focus: str = "bugs,performance,style"

class ExplainReq(BaseModel):
    code: str
    language: str

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate(req: GenerateReq):
    """Сгенерировать код по описанию."""
    model = req.model or CODEGEN_MODEL
    context_part = f"\n\nСуществующий код для контекста:\n```{req.language}\n{req.context}\n```" if req.context else ""

    prompt = (
        f"Ты эксперт по {req.language}. Напиши чистый, хорошо прокомментированный код.\n"
        f"Задача: {req.description}{context_part}\n\n"
        f"Требования:\n"
        f"- Язык: {req.language}\n"
        f"- Обработка ошибок обязательна\n"
        f"- Комментарии на русском\n"
        f"- Верни ТОЛЬКО код в блоке ```{req.language}```\n"
    )

    raw  = await call_codegen(prompt, model)
    code = extract_code(raw, req.language)

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.execute(
        "INSERT INTO generations (language, description, code, model) VALUES (?,?,?,?)",
        (req.language, req.description, code, model)
    )
    gen_id = cur.lastrowid
    conn.commit(); conn.close()

    logger.info(f"Код #{gen_id} сгенерирован: {req.language} | {req.description[:50]}")
    return {"id": gen_id, "language": req.language, "code": code, "model": model}

@router.post("/debug")
async def debug(req: DebugReq):
    """Отладка кода — передаёт ошибку в LLM для исправления."""
    model = req.model or CODEGEN_MODEL
    prompt = (
        f"Исправь ошибку в следующем {req.language} коде.\n\n"
        f"Код:\n```{req.language}\n{req.code}\n```\n\n"
        f"Ошибка:\n```\n{req.error}\n```\n\n"
        "Объясни причину ошибки и верни ИСПРАВЛЕННЫЙ код в блоке кода."
    )

    raw        = await call_codegen(prompt, model)
    fixed_code = extract_code(raw, req.language)

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO debug_sessions (generation_id, error_text, fixed_code) VALUES (?,?,?)",
        (req.generation_id, req.error, fixed_code)
    )
    if req.generation_id:
        conn.execute("UPDATE generations SET iterations=iterations+1 WHERE id=?", (req.generation_id,))
    conn.commit(); conn.close()

    # Извлечь объяснение (всё что не код)
    explanation = re.sub(rf"```.*?```", "", raw, flags=re.DOTALL).strip()

    return {"fixed_code": fixed_code, "explanation": explanation, "model": model}

@router.post("/review")
async def review(req: ReviewReq):
    """Code review — анализ качества кода."""
    prompt = (
        f"Проведи code review для следующего {req.language} кода.\n"
        f"Аспекты для проверки: {req.focus}\n\n"
        f"Код:\n```{req.language}\n{req.code}\n```\n\n"
        "Структура ответа:\n"
        "1. Найденные проблемы (с номерами строк если возможно)\n"
        "2. Рекомендации по улучшению\n"
        "3. Оценка качества (1-10)\n"
        "Ответ на русском языке."
    )
    review_text = await call_codegen(prompt)
    return {"review": review_text, "language": req.language}

@router.post("/explain")
async def explain(req: ExplainReq):
    """Объяснить что делает код."""
    prompt = (
        f"Объясни что делает следующий {req.language} код простым языком на русском.\n"
        f"```{req.language}\n{req.code}\n```\n\n"
        "Структура:\n1. Краткое описание\n2. Что делает каждая часть\n3. Примеры использования"
    )
    explanation = await call_codegen(prompt)
    return {"explanation": explanation}

@router.get("/history")
def history(limit: int = 20):
    """История генераций."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, language, description, model, iterations, created_at FROM generations ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return {"generations": [dict(r) for r in rows]}

@router.get("/history/{gen_id}")
def get_generation(gen_id: int):
    """Получить конкретную генерацию с кодом."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM generations WHERE id=?", (gen_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Генерация #{gen_id} не найдена")
    return dict(row)

@router.get("/models")
async def list_models():
    """Список доступных моделей в Ollama."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            return {"models": models, "current": CODEGEN_MODEL}
    except Exception:
        return {"models": [], "current": CODEGEN_MODEL, "error": "Ollama недоступна"}

@router.get("/health")
def health():
    return {"agent": "Гефест", "alive": True, "model": CODEGEN_MODEL}

app = FastAPI(title="Гефест — Генерация кода")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8012)
