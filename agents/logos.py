"""
agents/logos.py — Агент Логос (форматирование ответов). Сиен 3.0.0.

Новое:
  • Учитывает эмоцию пользователя (joy/sadness/anger/fear/neutral) —
    выбирает соответствующий тон и смайл.
  • Стиль ответа настраивается через профиль (formal / friendly / minimal).
  • Убрано дублирование app = FastAPI().
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Optional

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

logger = logging.getLogger("logos")
logging.basicConfig(level=logging.INFO, format="[ЛОГОС] %(message)s")

router = APIRouter(prefix="/logos", tags=["logos"])

# ══════════════════════════════════════════════════════════════════
# Словари и шаблоны
# ══════════════════════════════════════════════════════════════════

AGENT_EMOJI = {
    "argus": "🔍", "kronos": "🔐", "ahill": "🛡️", "fenix": "🔥", "logos": "💬",
    "wen": "📅", "plutos": "💰", "apollo": "🎬", "hermes": "📢", "kun": "🎓",
    "master": "💪", "musa": "✍️", "kallio": "🎨", "hefest": "⚙️", "avto": "🖱️",
    "huei": "🖼️", "meng": "🎞️", "eho": "🔊", "irida": "📱", "mnemon": "🌐",
    "dike": "📊", "system": "⚙️",
}

STATUS_EMOJI = {
    "ok": "✅", "success": "✅", "error": "❌", "warning": "⚠️",
    "degraded": "🟡", "failed": "🔴", "initializing": "🔄",
    "switched": "🔀", "created": "➕", "deleted": "🗑️", "info": "ℹ️",
}

EMOTION_PREFIX = {
    "joy":     ["😊 Отлично!", "🎉 Замечательно!", "✨"],
    "sadness": ["Понимаю…", "Я здесь.", "🤗"],
    "anger":   ["Слышу тебя.", "Давай разберёмся.", "Окей."],
    "fear":    ["Всё в порядке.", "Без паники.", "Шаг за шагом."],
    "neutral": [""],
}

CYBER_QUOTES = [
    "«Информация — валюта будущего.»",
    "«В сети нет тайн, есть лишь уровни доступа.»",
    "«Система спит. Агент никогда.»",
    "«Данные — нейтральны. Интерпретация — нет.»",
]

ACTION_TEMPLATES = {
    "proxy_status": "🛡️ **Статус прокси:**\n{content}",
    "switch_server": "🔀 **Сервер переключён:**\n{content}",
    "search_web":    "🔍 **Результаты поиска:**\n{content}",
    "create_task":   "📅 **Задача создана:**\n{content}",
    "list_tasks":    "📋 **Список задач:**\n{content}",
    "delete_task":   "🗑️ **Задача удалена:**\n{content}",
    "get_secret":    "🔐 **Секрет получен:**\n{content}",
    "clarify":       "💬 **Уточнение:**\n{content}",
    "error":         "❌ **Ошибка:**\n{content}",
}


# ══════════════════════════════════════════════════════════════════
# Модели
# ══════════════════════════════════════════════════════════════════

class FormatRequest(BaseModel):
    agent: str
    action: str
    raw_data: Any
    status: str = "ok"
    include_quote: bool = False
    context: Optional[str] = None
    emotion: str = "neutral"
    style: str = "friendly"      # friendly / formal / minimal


class FormatResponse(BaseModel):
    formatted: str
    plain: str
    emoji_status: str


# ══════════════════════════════════════════════════════════════════
# Утилиты форматирования
# ══════════════════════════════════════════════════════════════════

def format_dict(data: dict, indent: int = 0) -> str:
    lines = []
    pad = "  " * indent
    for key, value in data.items():
        key_str = str(key).replace("_", " ").capitalize()
        if isinstance(value, dict):
            lines.append(f"{pad}• **{key_str}:**")
            lines.append(format_dict(value, indent + 1))
        elif isinstance(value, list):
            lines.append(
                f"{pad}• **{key_str}:** {', '.join(str(v) for v in value)}"
            )
        elif value is None:
            lines.append(f"{pad}• **{key_str}:** —")
        else:
            lines.append(f"{pad}• **{key_str}:** `{value}`")
    return "\n".join(lines)


def format_list(items: list) -> str:
    if not items:
        return "_Список пуст_"
    lines = []
    for i, item in enumerate(items, 1):
        if isinstance(item, dict):
            title = (item.get("title") or item.get("name")
                     or item.get("text", f"Элемент {i}"))
            status = item.get("status", "")
            icon = STATUS_EMOJI.get(status, "•")
            lines.append(f"{i}. {icon} **{title}**")
            if "due" in item and item["due"]:
                lines.append(f"   ⏰ `{item['due']}`")
        else:
            lines.append(f"{i}. {item}")
    return "\n".join(lines)


def build_content(raw_data: Any, action: str) -> str:
    if raw_data is None:
        return "_Нет данных_"
    if isinstance(raw_data, str):
        return raw_data
    if isinstance(raw_data, list):
        return format_list(raw_data)
    if isinstance(raw_data, dict):
        if "message" in raw_data and len(raw_data) <= 3:
            return raw_data["message"]
        return format_dict(raw_data)
    return str(raw_data)


def strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    text = re.sub(r"_(.*?)_", r"\1", text)
    return text


def apply_style(text: str, style: str) -> str:
    if style == "minimal":
        # Убираем заголовки и декор
        return strip_markdown(text)
    if style == "formal":
        # Оставляем markdown, но убираем смайлы (кроме статусных)
        return re.sub(r"[😊🎉✨🤗]", "", text).strip()
    return text  # friendly = как есть


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

@router.post("/format", response_model=FormatResponse)
async def format_response(req: FormatRequest):
    agent_icon = AGENT_EMOJI.get(req.agent, "🤖")
    status_icon = STATUS_EMOJI.get(req.status, "ℹ️")

    content = build_content(req.raw_data, req.action)
    template = ACTION_TEMPLATES.get(req.action, "{content}")
    body = template.format(content=content)

    header = f"{agent_icon} **[{req.agent.upper()}]** {status_icon}"
    if req.context:
        header += f" _{req.context}_"

    parts = []
    # Префикс по эмоции (для friendly)
    if req.style == "friendly" and req.emotion in EMOTION_PREFIX:
        prefix = random.choice(EMOTION_PREFIX[req.emotion])
        if prefix:
            parts.append(prefix)
    parts.extend([header, "", body])

    if req.include_quote:
        parts += ["", f"> {random.choice(CYBER_QUOTES)}"]

    formatted = "\n".join(parts)
    formatted = apply_style(formatted, req.style)
    plain = strip_markdown(formatted)

    return FormatResponse(
        formatted=formatted,
        plain=plain,
        emoji_status=status_icon,
    )


@router.post("/clarify")
async def clarify(question: str):
    return FormatResponse(
        formatted=f"💬 **Уточнение нужно:**\n{question}",
        plain=f"Уточнение нужно: {question}",
        emoji_status="💬",
    )


@router.post("/error")
async def format_error(message: str, agent: str = "system"):
    agent_icon = AGENT_EMOJI.get(agent, "⚙️")
    formatted = f"{agent_icon} ❌ **Ошибка в [{agent.upper()}]:**\n{message}"
    return FormatResponse(
        formatted=formatted,
        plain=f"Ошибка в [{agent}]: {message}",
        emoji_status="❌",
    )


@router.get("/health")
async def health():
    return {"agent": "Логос", "alive": True}


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Логос — Оформление")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
