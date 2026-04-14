"""
agents/fenix.py — Агент Феникс (парсер намерений). Сиен 3.0.0.

Изменения:
  • Принимает context (история диалога) и emotion (эмоция пользователя).
  • Использует core.llm_cache для кэширования повторных запросов к Ollama.
  • Убрано дублирование app = FastAPI() в конце файла.
  • Rule-based fallback расширен.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from core.llm_cache import LLMCache

logger = logging.getLogger("fenix")
logging.basicConfig(level=logging.INFO, format="[ФЕНИКС] %(message)s")

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:3b"
QWEN_MODEL = "qwen2.5:7b"

_cache = LLMCache(default_ttl=3600)  # 1 час для intent-парсинга

router = APIRouter(prefix="/fenix", tags=["fenix"])

SYSTEM_PROMPT = """Ты — парсер намерений системы Сиен. Определи, какому агенту отдать команду.

Доступные агенты:
- argus (поиск в интернете, анализ веб-страниц)
- wen (задачи, напоминания, календарь, почта)
- ahill (VPS, прокси, смена сервера)
- logos (форматирование, уточнение)
- plutos (инвестиции, крипта, акции)
- kun (обучение, RAG по документам)
- master (фитнес, тренировки, здоровье)
- musa (генерация контента, тексты, посты)
- hermes (партнёрский маркетинг, соцсети)
- apollo (генерация видео)
- huei (генерация изображений)
- meng (длинные видео)
- eho (TTS, озвучка)
- irida (Telegram-бот)
- hefest (генерация кода)
- avto (макросы)
- mnemon (перевод)
- dike (бюджет, бухгалтерия)
- kallio (медиа)
- cronos (секреты)

Верни СТРОГО JSON такого формата:
{"agent": "имя", "action": "название_действия", "params": {...}, "confidence": 0.0-1.0, "raw_intent": "краткое описание"}

Никаких комментариев вокруг, только JSON.
"""


class ParseRequest(BaseModel):
    text: str
    context: str = ""
    emotion: str = "neutral"


class ParseResponse(BaseModel):
    agent: str
    action: str
    params: dict[str, Any] = {}
    confidence: float = 0.0
    raw_intent: str = ""
    raw_llm: str = ""


# ══════════════════════════════════════════════════════════════════
# Ollama
# ══════════════════════════════════════════════════════════════════

async def call_ollama(text: str, context: str = "", emotion: str = "neutral") -> str:
    """Вызов Ollama с учётом контекста диалога и эмоции."""
    prompt_parts = [SYSTEM_PROMPT]
    if context:
        prompt_parts.append(context)
    if emotion and emotion != "neutral":
        prompt_parts.append(f"[Эмоция пользователя: {emotion}]")
    prompt_parts.append(f"Команда пользователя: {text}")
    prompt_parts.append("Ответ (JSON):")
    full_prompt = "\n\n".join(prompt_parts)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 200},
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("response", "")
    except Exception as e:
        logger.warning(f"Ollama недоступна: {e}")
    return ""


def extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return {}


# ══════════════════════════════════════════════════════════════════
# Rule-based fallback
# ══════════════════════════════════════════════════════════════════

RULE_MAP = [
    # Задачи и напоминания
    (["мои задачи", "список задач", "покажи задачи", "все задачи", "задачи"],
     {"agent": "wen", "action": "list_tasks"}),
    (["напомни", "создай задачу", "добавь задачу", "новая задача"],
     {"agent": "wen", "action": "create_task"}),
    (["удали задачу"],
     {"agent": "wen", "action": "delete_task"}),

    # Поиск
    (["найди", "поищи", "что такое", "кто такой", "расскажи о", "загугли", "поиск"],
     {"agent": "argus", "action": "search_web"}),

    # VPS / прокси
    (["смени сервер", "поменяй vpn", "другой сервер", "переключи прокси"],
     {"agent": "ahill", "action": "switch_server"}),
    (["статус прокси", "статус vpn", "мой ip", "какой ip"],
     {"agent": "ahill", "action": "proxy_status"}),

    # Погода / новости (через поиск — нет отдельного хука)
    (["погода", "прогноз"],
     {"agent": "argus", "action": "search_web"}),
    (["новости", "что нового"],
     {"agent": "argus", "action": "search_web"}),

    # Перевод
    (["переведи", "перевод", "translate"],
     {"agent": "mnemon", "action": "translate"}),

    # Озвучка
    (["озвучь", "прочитай", "произнеси"],
     {"agent": "eho", "action": "tts"}),

    # Генерация
    (["нарисуй", "сгенерируй картинку", "сгенерируй изображение"],
     {"agent": "huei", "action": "generate"}),
    (["напиши код", "сгенерируй код"],
     {"agent": "hefest", "action": "generate_code"}),
    (["сделай видео", "сгенерируй видео"],
     {"agent": "apollo", "action": "generate"}),

    # Финансы
    (["баланс", "портфель", "мои акции"],
     {"agent": "plutos", "action": "portfolio"}),

    # Почта
    (["почта", "письма", "входящие"],
     {"agent": "wen", "action": "inbox"}),

    # Обучение
    (["научи", "объясни", "урок"],
     {"agent": "kun", "action": "lesson"}),

    # Тренировка / здоровье
    (["тренировка", "спорт", "сон", "питание"],
     {"agent": "master", "action": "status"}),

    # Статус системы
    (["статус агентов", "агенты", "список агентов"],
     {"agent": "argus", "action": "status"}),
]


def rule_based_fallback(text: str) -> dict:
    t = text.lower()
    for keywords, result in RULE_MAP:
        if any(kw in t for kw in keywords):
            return {**result, "params": {"query": text}, "confidence": 0.65}
    return {"agent": "logos", "action": "clarify",
            "params": {"question": "Уточни запрос."},
            "confidence": 0.2}


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

@router.post("/parse", response_model=ParseResponse)
async def parse_intent(req: ParseRequest):
    logger.info(f"Парсинг: '{req.text[:60]}'")

    # Кэш только если без контекста (контекст делает промпт уникальным)
    cache_key = None
    if not req.context:
        cache_key = f"fenix::{req.emotion}::{req.text.strip().lower()}"
        cached = _cache.get(cache_key)
        if cached is not None:
            try:
                parsed = json.loads(cached)
                return ParseResponse(
                    agent=parsed.get("agent", "logos"),
                    action=parsed.get("action", "clarify"),
                    params=parsed.get("params", {}),
                    confidence=parsed.get("confidence", 0.8),
                    raw_intent=parsed.get("raw_intent", ""),
                    raw_llm="[cached]",
                )
            except json.JSONDecodeError:
                pass

    raw_llm = await call_ollama(req.text, req.context, req.emotion)

    if raw_llm:
        parsed = extract_json(raw_llm)
        if parsed and "agent" in parsed and "action" in parsed:
            logger.info(f"LLM → {parsed['agent']}.{parsed['action']}")
            if cache_key:
                _cache.set(cache_key, json.dumps(parsed, ensure_ascii=False),
                           model=OLLAMA_MODEL, agent="fenix")
            return ParseResponse(
                agent=parsed.get("agent", "logos"),
                action=parsed.get("action", "clarify"),
                params=parsed.get("params", {}),
                confidence=parsed.get("confidence", 0.8),
                raw_intent=parsed.get("raw_intent", ""),
                raw_llm=raw_llm[:200],
            )
        logger.warning("LLM вернула невалидный JSON")

    result = rule_based_fallback(req.text)
    return ParseResponse(**result,
                         raw_llm=raw_llm[:200] if raw_llm else "ollama_unavailable")


@router.get("/health")
async def health():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{OLLAMA_URL}/api/tags",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                ollama_ok = r.status == 200
    except Exception:
        ollama_ok = False
    return {
        "agent": "Феникс", "alive": True,
        "ollama": ollama_ok, "model": OLLAMA_MODEL,
        "qwen_model": QWEN_MODEL,
        "cache": _cache.stats(),
    }


@router.get("/models")
async def list_models():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{OLLAMA_URL}/api/tags") as r:
                data = await r.json()
                return {"models": [m["name"] for m in data.get("models", [])]}
    except Exception:
        return {"models": [], "error": "Ollama недоступна"}


@router.post("/cache/clear")
async def clear_cache():
    return {"deleted": _cache.clear()}


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Феникс — Парсер намерений")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
