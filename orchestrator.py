"""
Сиен 3.0.0 — Оркестратор.

Изменения против 2.0 Beta:
  • Полный реестр из 20+ агентов (раньше — только 4).
  • Единственный persistent httpx.AsyncClient (нет утечек, connection pool).
  • Диалоговая память: при получении команды вытягиваются последние N сообщений
    из таблицы conversation_history и добавляются в промпт Фениксу.
  • Эмоциональный анализ (core.emotion) пробрасывается в Логос.
  • LLM-кэш (core.llm_cache) используется для парсинга намерений.
  • Graceful shutdown: SIGTERM/SIGINT корректно закрывают БД, httpx и WS.
  • Исправлен импорт: web.dashboard (а не из корня).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# ── Внутренние модули ──────────────────────────────────────────────
from web.dashboard import router as dashboard_router
from core.llm_cache import LLMCache
from core.emotion import analyze_emotion
from core.graceful_shutdown import shutdown_manager

logging.basicConfig(level=logging.INFO, format="[ОРКЕСТРАТОР] %(message)s")
logger = logging.getLogger("orchestrator")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DIALOG_DB = DATA_DIR / "sien.db"

# ══════════════════════════════════════════════════════════════════
# Диалоговая память
# ══════════════════════════════════════════════════════════════════

DIALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,        -- user | assistant | system
    text       TEXT    NOT NULL,
    emotion    TEXT,                    -- joy/sadness/anger/fear/neutral
    agent      TEXT,                    -- какой агент ответил
    timestamp  REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_ch_session_ts
    ON conversation_history(session_id, timestamp DESC);
"""


def _init_dialog_db() -> None:
    with sqlite3.connect(str(DIALOG_DB)) as conn:
        conn.executescript(DIALOG_SCHEMA)


def save_message(session_id: str, role: str, text: str,
                 emotion: str | None = None, agent: str | None = None) -> None:
    try:
        with sqlite3.connect(str(DIALOG_DB)) as conn:
            conn.execute(
                "INSERT INTO conversation_history (session_id, role, text, emotion, agent) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, text, emotion, agent),
            )
    except Exception as e:
        logger.warning(f"Не удалось сохранить сообщение: {e}")


def load_context(session_id: str, n: int = 10) -> list[dict]:
    """Загружает последние N сообщений сессии в хронологическом порядке."""
    try:
        with sqlite3.connect(str(DIALOG_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, text, emotion, agent FROM conversation_history "
                "WHERE session_id=? ORDER BY timestamp DESC LIMIT ?",
                (session_id, n),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
# Реестр агентов (ПОЛНЫЙ!)
# ══════════════════════════════════════════════════════════════════

AGENTS: dict[str, str] = {
    "cronos":  "http://localhost:8001",
    "ahill":   "http://localhost:8003",
    "fenix":   "http://localhost:8004",
    "logos":   "http://localhost:8005",
    "wen":     "http://localhost:8006",
    "kun":     "http://localhost:8007",
    "master":  "http://localhost:8008",
    "plutos":  "http://localhost:8009",
    "musa":    "http://localhost:8010",
    "kallio":  "http://localhost:8011",
    "hefest":  "http://localhost:8012",
    "avto":    "http://localhost:8013",
    "huei":    "http://localhost:8014",
    "meng":    "http://localhost:8015",
    "eho":     "http://localhost:8016",
    "irida":   "http://localhost:8017",
    "apollo":  "http://localhost:8018",
    "hermes":  "http://localhost:8019",
    "dike":    "http://localhost:8020",
    "mnemon":  "http://localhost:8021",
    "argus":   "http://localhost:8022",
}

# У каждого агента свой префикс health-эндпоинта
HEALTH_URLS: dict[str, str] = {
    "cronos":  "http://localhost:8001/health",
    "ahill":   "http://localhost:8003/proxy/health",
    "fenix":   "http://localhost:8004/fenix/health",
    "logos":   "http://localhost:8005/logos/health",
    "wen":     "http://localhost:8006/tasks/health",
    "kun":     "http://localhost:8007/professor/health",
    "master":  "http://localhost:8008/trainer/health",
    "plutos":  "http://localhost:8009/invest/health",
    "musa":    "http://localhost:8010/content/health",
    "kallio":  "http://localhost:8011/media/health",
    "hefest":  "http://localhost:8012/code/health",
    "avto":    "http://localhost:8013/macros/health",
    "huei":    "http://localhost:8014/image/health",
    "meng":    "http://localhost:8015/video_long/health",
    "eho":     "http://localhost:8016/tts/health",
    "irida":   "http://localhost:8017/telegram/health",
    "apollo":  "http://localhost:8018/video/health",
    "hermes":  "http://localhost:8019/affiliate/health",
    "dike":    "http://localhost:8020/accounting/health",
    "mnemon":  "http://localhost:8021/translate/health",
    "argus":   "http://localhost:8022/monitor/health",
}

# ══════════════════════════════════════════════════════════════════
# Глобальный state
# ══════════════════════════════════════════════════════════════════

connected_clients: list[WebSocket] = []
_http_client: httpx.AsyncClient | None = None
_llm_cache = LLMCache(default_ttl=3600)


def get_http() -> httpx.AsyncClient:
    assert _http_client is not None, "HTTP-клиент не инициализирован"
    return _http_client


# ══════════════════════════════════════════════════════════════════
# Lifespan (startup/shutdown)
# ══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _init_dialog_db()
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=3.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    asyncio.create_task(periodic_status_broadcast())
    logger.info("Оркестратор запущен. Реестр: %d агентов.", len(AGENTS))

    # Graceful shutdown: регистрируем задачу закрытия
    shutdown_manager.register(_graceful_close)

    yield

    await _graceful_close()


async def _graceful_close():
    """Выполняется при SIGTERM/SIGINT/lifespan shutdown."""
    logger.info("Остановка оркестратора...")
    # Закрываем все WS-соединения
    for ws in list(connected_clients):
        try:
            await ws.close(code=1001, reason="server shutting down")
        except Exception:
            pass
    connected_clients.clear()
    # Закрываем httpx
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
    logger.info("Оркестратор остановлен корректно.")


# ══════════════════════════════════════════════════════════════════
# FastAPI app
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Сиен 3.0.0 — Оркестратор", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # allow_origins=["*"] не работает с credentials и не покрывает "null" (file://).
    # allow_origin_regex=".*" явно покрывает всё, включая null-origin.
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(dashboard_router)

# Статика HUD (если запускаем как PWA)
HUD_DIR = BASE_DIR / "hud"
if HUD_DIR.exists():
    app.mount("/hud", StaticFiles(directory=str(HUD_DIR), html=True), name="hud")

STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ══════════════════════════════════════════════════════════════════
# WebSocket /ws — HUD
# ══════════════════════════════════════════════════════════════════

async def broadcast(message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False)
    dead: list[WebSocket] = []
    for ws in connected_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in connected_clients:
            connected_clients.remove(ws)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    logger.info(f"HUD подключён. Всего клиентов: {len(connected_clients)}")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            if mtype in ("get_agents_status", "get_agents"):
                await check_all_agents(websocket)
            elif mtype == "command":
                asyncio.create_task(handle_command(
                    msg.get("text", ""),
                    websocket,
                    session_id=msg.get("session_id", "default"),
                ))
            elif mtype == "reset_context":
                # Клиент сам создаёт новый session_id и шлёт в следующем сообщении
                await websocket.send_text(json.dumps(
                    {"type": "context_reset", "ok": True}, ensure_ascii=False
                ))
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        logger.info("HUD отключился")


async def check_all_agents(ws: WebSocket) -> None:
    client = get_http()
    for name, health_url in HEALTH_URLS.items():
        try:
            r = await client.get(health_url, timeout=3.0)
            status = "ok" if r.status_code == 200 else "error"
            try:
                task = r.json().get("task", "—")
            except Exception:
                task = "—"
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout):
            status, task = "offline", "Недоступен"
        except Exception:
            status, task = "error", "Ошибка"
        await ws.send_text(json.dumps(
            {"type": "agent_status", "agent": name, "status": status, "task": task},
            ensure_ascii=False,
        ))


# ══════════════════════════════════════════════════════════════════
# DuckDuckGo поиск (без ключа)
# ══════════════════════════════════════════════════════════════════

async def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    results: list[dict] = []
    client = get_http()
    try:
        r = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=10.0,
        )
        text = r.text
        titles = re.findall(
            r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', text, re.DOTALL)
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>', text, re.DOTALL)
        urls = re.findall(
            r'class="result__url"[^>]*>(.*?)</a>', text, re.DOTALL)
        for i in range(min(max_results, len(titles))):
            title = re.sub(r'<[^>]+>', '', titles[i]).strip()
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
            url = urls[i].strip() if i < len(urls) else ""
            if title:
                results.append({"title": title, "snippet": snippet, "url": url})
    except Exception as e:
        logger.warning(f"DuckDuckGo: {e}")
    return results


def format_search_results(query: str, results: list[dict]) -> str:
    if not results:
        return f"По запросу «{query}» ничего не найдено."
    lines = [f"🔍 Результаты поиска: «{query}»\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:120]}...")
        if r.get("url"):
            lines.append(f"   🔗 {r['url']}")
        lines.append("")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Диспетчер команд
# ══════════════════════════════════════════════════════════════════

async def handle_command(text: str, ws: WebSocket, session_id: str = "default") -> None:
    if not text.strip():
        return

    # 1. Анализ эмоции
    emotion = analyze_emotion(text)

    # 2. Сохраняем пользовательское сообщение
    save_message(session_id, "user", text, emotion=emotion)

    # 3. Загружаем контекст (последние 10 сообщений) и передаём в Феникс
    ctx = load_context(session_id, n=10)
    ctx_block = ""
    if ctx:
        parts = []
        for m in ctx[:-1]:   # без текущего сообщения
            who = "Пользователь" if m["role"] == "user" else "Сиен"
            parts.append(f"{who}: {m['text']}")
        if parts:
            ctx_block = "История диалога:\n" + "\n".join(parts) + "\n\n"

    client = get_http()
    try:
        # 4. Парсим намерение через Феникс (с кэшем)
        cache_key = f"intent::{text.strip().lower()}"
        cached = _llm_cache.get(cache_key)
        if cached is not None:
            intent = json.loads(cached)
        else:
            try:
                r = await client.post(
                    f"{AGENTS['fenix']}/fenix/parse",
                    json={"text": text, "context": ctx_block, "emotion": emotion},
                )
                intent = r.json()
                _llm_cache.set(cache_key, json.dumps(intent, ensure_ascii=False),
                               model="fenix", agent="orchestrator")
            except Exception as e:
                intent = local_fallback(text)
                logger.warning(f"Феникс недоступен, fallback: {e}")

        agent_name = intent.get("agent", "logos")
        action = intent.get("action", "clarify")
        params = intent.get("params", {})
        confidence = intent.get("confidence", 0.5)

        logger.info(
            f"Intent: {agent_name}.{action} conf={confidence:.2f} "
            f"emotion={emotion} text='{text[:40]}'"
        )

        formatted, result = await execute(agent_name, action, params, text, emotion)

        # 5. Сохраняем ответ ассистента
        save_message(session_id, "assistant", formatted, agent=agent_name)

        await ws.send_text(json.dumps({
            "type": "command_result",
            "agent": agent_name,
            "action": action,
            "result": result,
            "formatted": formatted,
            "emotion": emotion,
        }, ensure_ascii=False))

        await ws.send_text(json.dumps({
            "type": "agent_status",
            "agent": agent_name,
            "status": "ok",
            "task": action,
        }, ensure_ascii=False))

    except Exception as e:
        logger.error(f"handle_command: {e}", exc_info=True)
        await ws.send_text(json.dumps({
            "type": "error",
            "agent": "orchestrator",
            "message": f"Ошибка: {e}",
        }, ensure_ascii=False))


async def execute(agent: str, action: str, params: dict,
                  original_text: str, emotion: str = "neutral"):
    """Выполнение действия агента. Возвращает (formatted_str, result_dict)."""
    client = get_http()

    # ── ARGUS: поиск ────────────────────────────────────────────
    if agent == "argus" and action in ("search_web", "analyze_content"):
        query = params.get("query") or original_text
        results = await search_duckduckgo(query)
        return format_search_results(query, results), {"results": results}

    # ── AHILL: VPS ──────────────────────────────────────────────
    if agent == "ahill" and action == "proxy_status":
        try:
            r = await client.get(f"{AGENTS['ahill']}/proxy/status")
            d = r.json()
            return (
                f"🛡️ **Статус прокси:**\n"
                f"• Сервер: `{d.get('current_vps', '—')}`\n"
                f"• Хост: `{d.get('vps_host', '—')}`\n"
                f"• Задержка: `{d.get('latency_ms', '—')} ms`\n"
                f"• Статус: `{d.get('status', '—')}`\n"
                f"• Прокси: `{d.get('proxy', '—')}`"
            ), d
        except Exception as e:
            return f"❌ Ахилл недоступен: {e}", {}

    if agent == "ahill" and action == "switch_server":
        try:
            r = await client.post(f"{AGENTS['ahill']}/proxy/switch")
            d = r.json()
            return f"🔀 {d.get('message', 'Сервер переключён')}", d
        except Exception as e:
            return f"❌ Ошибка переключения: {e}", {}

    # ── WEN: задачи ─────────────────────────────────────────────
    if agent == "wen" and action in ("create_task", "set_reminder"):
        title = params.get("title") or params.get("text") or original_text
        due = params.get("due") or params.get("time")
        try:
            r = await client.post(f"{AGENTS['wen']}/tasks/create", json={
                "title": title, "due": due,
                "description": params.get("description", ""),
            })
            d = r.json()
            due_str = f"\n• Срок: `{due}`" if due else ""
            return f"📅 **Задача создана:**\n• `{title}`{due_str}", d
        except Exception as e:
            return f"❌ Ошибка создания задачи: {e}", {}

    if agent == "wen" and action == "list_tasks":
        try:
            r = await client.get(f"{AGENTS['wen']}/tasks/list")
            tasks = r.json()
            pending = [t for t in tasks if t.get("status") == "pending"]
            if not pending:
                return "📋 Задач нет.", {"tasks": []}
            lines = ["📋 **Активные задачи:**\n"]
            for t in pending[:10]:
                due = f" ⏰ `{t['due']}`" if t.get("due") else ""
                lines.append(f"• [{t['id']}] {t['title']}{due}")
            return "\n".join(lines), {"tasks": pending}
        except Exception as e:
            return f"❌ Ошибка загрузки задач: {e}", {}

    if agent == "wen" and action == "delete_task":
        task_id = params.get("id") or params.get("task_id")
        if not task_id:
            return "❌ Укажи ID задачи.", {}
        try:
            r = await client.delete(f"{AGENTS['wen']}/tasks/delete/{task_id}")
            return f"🗑️ Задача #{task_id} удалена.", r.json()
        except Exception as e:
            return f"❌ Ошибка удаления: {e}", {}

    # ── LOGOS: форматирование (с учётом эмоции) ─────────────────
    if agent == "logos" and action in ("clarify", "format_response"):
        try:
            r = await client.post(f"{AGENTS['logos']}/logos/format", json={
                "agent":    params.get("agent", "system"),
                "action":   params.get("action", action),
                "raw_data": params.get("raw_data", params.get("question", "")),
                "status":   params.get("status", "info"),
                "emotion":  emotion,
            })
            d = r.json()
            return d.get("formatted", ""), d
        except Exception:
            return str(params.get("raw_data", params.get("question", ""))), {}

    # ── Универсальный прокси для остальных агентов ──────────────
    if agent in AGENTS and action:
        try:
            url = f"{AGENTS[agent]}/{action.strip('/')}"
            r = await client.post(url, json=params, timeout=15.0)
            if r.status_code == 200:
                try:
                    d = r.json()
                    return json.dumps(d, ensure_ascii=False, indent=2), d
                except Exception:
                    return r.text[:500], {"text": r.text[:500]}
        except Exception as e:
            logger.debug(f"Универсальный вызов {agent}.{action}: {e}")

    # ── Неизвестное действие ────────────────────────────────────
    return await handle_unknown(original_text), {}


async def handle_unknown(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["привет", "hello", "hi", "здравствуй"]):
        return "👋 Привет! Я Сиен. Могу искать, управлять задачами, следить за VPS. Что нужно?"
    if any(w in t for w in ["время", "час", "сколько времени"]):
        now = datetime.now()
        return f"🕐 Сейчас **{now:%H:%M:%S}**, дата: **{now:%d.%m.%Y}**"
    if any(w in t for w in ["помощь", "помоги", "что умеешь"]):
        return (
            "⚙️ **Команды:**\n\n"
            "🔍 Поиск: «найди X»\n"
            "📅 Задачи: «напомни о X», «покажи задачи»\n"
            "🛡️ VPS: «статус прокси»\n"
        )
    results = await search_duckduckgo(text, max_results=3)
    return format_search_results(text, results)


def local_fallback(text: str) -> dict:
    t = text.lower()
    if any(w in t for w in ["найди", "поищи", "что такое", "кто такой", "расскажи"]):
        return {"agent": "argus", "action": "search_web", "params": {"query": text}, "confidence": 0.7}
    if any(w in t for w in ["напомни", "создай задачу", "встреча", "событие"]):
        return {"agent": "wen", "action": "create_task", "params": {"text": text}, "confidence": 0.7}
    if any(w in t for w in ["задачи", "список задач", "мои задачи"]):
        return {"agent": "wen", "action": "list_tasks", "params": {}, "confidence": 0.8}
    if any(w in t for w in ["смени сервер", "другой сервер"]):
        return {"agent": "ahill", "action": "switch_server", "params": {}, "confidence": 0.8}
    if any(w in t for w in ["статус", "прокси", "vpn", "ip"]):
        return {"agent": "ahill", "action": "proxy_status", "params": {}, "confidence": 0.7}
    return {"agent": "logos", "action": "clarify",
            "params": {"question": "Уточни запрос."}, "confidence": 0.2}


# ══════════════════════════════════════════════════════════════════
# REST-прокси для задач и health
# ══════════════════════════════════════════════════════════════════

@app.api_route("/tasks/{path:path}", methods=["GET", "POST", "DELETE", "PATCH"])
async def proxy_tasks(path: str, request: Request):
    url = f"{AGENTS['wen']}/tasks/{path}"
    try:
        client = get_http()
        body = await request.body()
        r = await client.request(
            method=request.method, url=url, content=body,
            headers={"Content-Type": "application/json"},
        )
        return Response(content=r.content, status_code=r.status_code,
                        media_type="application/json")
    except httpx.ConnectError:
        if request.method == "GET":
            return JSONResponse(content=[], status_code=200)
        return JSONResponse(content={"status": "agent_offline"}, status_code=503)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "orchestrator", "version": "3.0.0"}


@app.get("/agents/list")
async def agents_list():
    """Список зарегистрированных агентов с их базовыми URL."""
    return {"agents": AGENTS, "total": len(AGENTS)}


# ══════════════════════════════════════════════════════════════════
# Internal push: агенты → HUD через WS broadcast
# ══════════════════════════════════════════════════════════════════

@app.post("/internal/push")
async def internal_push(request: Request):
    """
    Принимает уведомление от агента (Wen, Plutos, Master и др.) и шлёт
    его всем подключённым WS-клиентам HUD.

    Использование:
        POST http://localhost:8000/internal/push
        {"type": "reminder", "agent": "wen", "message": "..."}
    """
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "JSON object required"}, status_code=400)
        # Гарантируем, что type есть (для роутинга на стороне клиента)
        payload.setdefault("type", "notification")
        await broadcast(payload)
        return {"status": "ok", "delivered_to": len(connected_clients)}
    except Exception as e:
        logger.error(f"internal_push: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ══════════════════════════════════════════════════════════════════
# Периодический статус
# ══════════════════════════════════════════════════════════════════

async def periodic_status_broadcast():
    await asyncio.sleep(5)
    while True:
        if connected_clients:
            client = get_http()
            for name, health_url in HEALTH_URLS.items():
                try:
                    r = await client.get(health_url, timeout=3.0)
                    status = "ok" if r.status_code == 200 else "error"
                except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout):
                    status = "offline"
                except Exception:
                    status = "error"
                await broadcast({"type": "agent_status", "agent": name,
                                 "status": status, "task": "—"})
        await asyncio.sleep(30)
