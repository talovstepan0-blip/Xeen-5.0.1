"""
web/dashboard.py — Веб-дашборд Сиен 3.0.0.

Изменения против 2.0 Beta:
  • TMPL_DIR указывает в две локации: templates/ и hud/ — шаблоны найдутся всегда.
  • Исправлен импорт local_commands (правильное имя функции).
  • /dashboard/system — мониторинг CPU/RAM/VRAM (psutil + pynvml).
  • /dashboard/api/2fa/setup, /verify — двухфакторная аутентификация (TOTP).
  • /dashboard/api/conversations — история диалогов (с дешифрованием).
  • Все шаблоны ищутся сначала в templates/, потом в hud/.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("dashboard")

router = APIRouter(prefix="/dashboard")

# ── Пути ───────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
TMPL_DIRS = [BASE_DIR / "templates", BASE_DIR / "hud", BASE_DIR]
DB_PATH = DATA_DIR / "sien.db"


def find_template(name: str) -> Optional[Path]:
    """Ищет шаблон в нескольких директориях."""
    for d in TMPL_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


# ══════════════════════════════════════════════════════════════════
# SQLite: таблицы профиля и истории
# ══════════════════════════════════════════════════════════════════

PROFILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profile (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT    NOT NULL UNIQUE,
    value      TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS local_command_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    command_name TEXT NOT NULL,
    raw_text     TEXT NOT NULL,
    params_json  TEXT NOT NULL DEFAULT '{}',
    confidence   REAL NOT NULL DEFAULT 0.0,
    ok           INTEGER NOT NULL DEFAULT 1,
    error_msg    TEXT,
    executed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cmd_history_ts
    ON local_command_history(executed_at DESC);

CREATE TABLE IF NOT EXISTS two_factor (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    secret      TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_profile_db() -> None:
    with get_db() as conn:
        conn.executescript(PROFILE_SCHEMA)


def profile_get(key: str, default: Any = None) -> Any:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM user_profile WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def profile_set(key: str, value: Any) -> None:
    val_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    with get_db() as conn:
        conn.execute(
            """INSERT INTO user_profile (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value, updated_at=excluded.updated_at""",
            (key, val_str),
        )


def profile_load_all() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            result[row["key"]] = row["value"]
    return result


# ══════════════════════════════════════════════════════════════════
# Pydantic
# ══════════════════════════════════════════════════════════════════

class CommandHistoryEntry(BaseModel):
    command_name: str
    raw_text: str
    params: dict = {}
    confidence: float = 0.0
    ok: bool = True
    error_msg: Optional[str] = None


class TOTPVerify(BaseModel):
    code: str


# ══════════════════════════════════════════════════════════════════
# Страницы
# ══════════════════════════════════════════════════════════════════


# ── Сиен 3.0: главная SPA dashboard ──
@router.get("/", response_class=HTMLResponse)
@router.get("/v3", response_class=HTMLResponse)
def dashboard_spa() -> HTMLResponse:
    tmpl = find_template("dashboard.html")
    if tmpl is None:
        return HTMLResponse("<h1>dashboard.html не найден</h1>", status_code=404)
    return HTMLResponse(tmpl.read_text(encoding="utf-8"))


# ── Секреты через Кронос ──
KRONOS_URL_DASH = "http://localhost:8001"

@router.get("/api/secrets/list")
async def secrets_list():
    """Список известных ключей (без значений)."""
    try:
        async with httpx.AsyncClient(timeout=3) as cl:
            r = await cl.get(f"{KRONOS_URL_DASH}/secrets/list")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    # Fallback: читаем локальный файл, если Кронос не запущен
    return {"keys": {}, "warning": "Кронос недоступен. Ключи будут сохранены в data/secrets_fallback.json (не шифровано!)"}


@router.post("/api/secrets/set")
async def secrets_set(request: Request):
    data = await request.json()
    key = data.get("key")
    value = data.get("value")
    if not key or value is None:
        raise HTTPException(400, "key и value обязательны")
    try:
        async with httpx.AsyncClient(timeout=3) as cl:
            r = await cl.post(f"{KRONOS_URL_DASH}/secrets/set",
                              json={"key": key, "value": value})
            if r.status_code == 200:
                return {"status": "saved", "via": "kronos"}
    except Exception:
        pass
    # Fallback в файл (предупреждение пользователю уже отдано в list)
    import json as _j
    fp = Path("data/secrets_fallback.json")
    fp.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if fp.exists():
        try: existing = _j.loads(fp.read_text(encoding="utf-8"))
        except Exception: existing = {}
    existing[key] = value
    fp.write_text(_j.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "saved", "via": "fallback_file", "warning": "Кронос недоступен, данные НЕ зашифрованы"}



# ── Плагины: список и тест ──
@router.get("/api/plugins/list")
def plugins_list():
    """Возвращает список всех core и productivity плагинов с проверкой загрузки."""
    import importlib
    result = []

    plugins_core = ['weather', 'news', 'tasks', 'notes', 'calendar',
                    'scheduler', 'settings', 'logger', 'cache_helper', 'sentiment']
    plugins_prod = ['kanban', 'eisenhower_matrix', 'project_kanban', 'macros',
                    'goals', 'routines', 'productivity_stats', 'focus_mode',
                    'daily_planner', 'habit_checklist']

    for name in plugins_core:
        try:
            importlib.import_module(f'plugins.core.{name}')
            result.append({'category': 'core', 'name': name, 'loaded': True})
        except Exception as e:
            result.append({'category': 'core', 'name': name, 'loaded': False, 'error': str(e)[:80]})

    for name in plugins_prod:
        try:
            importlib.import_module(f'plugins.productivity.{name}')
            result.append({'category': 'productivity', 'name': name, 'loaded': True})
        except Exception as e:
            result.append({'category': 'productivity', 'name': name, 'loaded': False, 'error': str(e)[:80]})

    ok = sum(1 for p in result if p.get('loaded'))
    return {'plugins': result, 'total': len(result), 'loaded': ok}


@router.post("/api/plugins/test")
async def plugins_test(request: Request):
    """Простой тест импорта и базовой инициализации плагина."""
    import importlib
    data = await request.json()
    category = data.get('category')
    name = data.get('name')
    if category not in ('core', 'productivity') or not name:
        raise HTTPException(400, 'category и name обязательны')
    try:
        mod = importlib.import_module(f'plugins.{category}.{name}')
        # Пробуем найти и инициализировать класс плагина
        for attr in dir(mod):
            cls = getattr(mod, attr)
            if isinstance(cls, type) and hasattr(cls, 'name'):
                try:
                    cls()
                    return {'ok': True, 'class': attr}
                except Exception as e:
                    return {'ok': False, 'class': attr, 'error': str(e)[:200]}
        return {'ok': True, 'message': 'module imported'}
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}



# ── Обучение: learned_commands ──
@router.get("/api/learning/list")
def learning_list():
    try:
        from core import learning as _l
        _l.init_db()
        items = _l.list_all(limit=200)
        for it in items:
            try:
                import json as _j
                it['action_data'] = _j.loads(it.get('action_data') or '{}')
            except Exception:
                pass
        return {"items": items, "count": len(items)}
    except Exception as e:
        return {"items": [], "count": 0, "error": str(e)}


@router.post("/api/learning/suggest")
async def learning_suggest(request: Request):
    try:
        from core import learning as _l
        data = await request.json()
        query = data.get("query", "")
        results = _l.suggest_with_llm(query, max_results=5)
        return {"query": query, "suggestions": results}
    except Exception as e:
        return {"query": "", "suggestions": [], "error": str(e)}


@router.post("/api/learning/learn")
async def learning_learn(request: Request):
    try:
        from core import learning as _l
        try:
            data = await request.json()
        except Exception as json_err:
            raise HTTPException(400, f"Некорректный JSON: {json_err}")
        phrase = data.get("phrase")
        action_type = data.get("action_type")
        action_data = data.get("action_data") or {}
        if not phrase or not action_type:
            raise HTTPException(400, "phrase и action_type обязательны")
        rid = _l.learn(phrase, action_type, action_data)
        return {"id": rid, "status": "learned"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Learning error: {e}")
        raise HTTPException(500, str(e))


@router.delete("/api/learning/{item_id}")
def learning_forget(item_id: int):
    from core import learning as _l
    ok = _l.forget(item_id)
    return {"status": "deleted" if ok else "not_found"}


@router.get("/profile", response_class=HTMLResponse)
async def profile_page():
    tmpl = find_template("profile.html")
    if tmpl:
        return HTMLResponse(tmpl.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>profile.html не найден</h1>", status_code=404)


@router.get("/system", response_class=HTMLResponse)
async def system_page():
    """Страница мониторинга CPU/RAM/VRAM."""
    tmpl = find_template("system.html")
    if tmpl:
        return HTMLResponse(tmpl.read_text(encoding="utf-8"))
    # Fallback: минимальный inline HTML с live-обновлением через fetch
    return HTMLResponse("""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Система — Сиен</title>
<style>
body{background:#0a0e1a;color:#e2e8f0;font-family:monospace;padding:20px;font-size:14px;line-height:1.6}
h1{color:#00d4ff;border-bottom:1px solid #1e2d45;padding-bottom:10px}
.card{background:#111827;border:1px solid #1e2d45;border-radius:8px;padding:16px;margin:12px 0}
.label{color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:1px}
.value{color:#00d4ff;font-size:22px;margin:4px 0}
.bar{background:#1e2d45;height:8px;border-radius:4px;overflow:hidden;margin-top:8px}
.bar-fill{height:100%;background:linear-gradient(90deg,#10b981,#00d4ff);transition:width .4s}
</style></head><body>
<h1>⚙ Мониторинг системы</h1>
<div class="card"><div class="label">CPU</div><div class="value" id="cpu">—</div><div class="bar"><div class="bar-fill" id="cpu-bar" style="width:0%"></div></div></div>
<div class="card"><div class="label">RAM</div><div class="value" id="ram">—</div><div class="bar"><div class="bar-fill" id="ram-bar" style="width:0%"></div></div></div>
<div class="card"><div class="label">VRAM (GPU)</div><div class="value" id="vram">—</div><div class="bar"><div class="bar-fill" id="vram-bar" style="width:0%"></div></div></div>
<div class="card"><div class="label">Диск</div><div class="value" id="disk">—</div><div class="bar"><div class="bar-fill" id="disk-bar" style="width:0%"></div></div></div>
<script>
async function tick(){try{
  const API_BASE=(location.protocol==='file:'||!location.host)?'http://localhost:8000':'';const r=await fetch(API_BASE+"/dashboard/api/system/stats",{mode:"cors",credentials:"omit"});const d=await r.json();
  document.getElementById("cpu").textContent=d.cpu_percent.toFixed(1)+" %";
  document.getElementById("cpu-bar").style.width=d.cpu_percent+"%";
  document.getElementById("ram").textContent=d.ram_used_gb.toFixed(2)+" / "+d.ram_total_gb.toFixed(2)+" ГБ ("+d.ram_percent.toFixed(1)+"%)";
  document.getElementById("ram-bar").style.width=d.ram_percent+"%";
  if(d.gpu){document.getElementById("vram").textContent=d.gpu.vram_used_gb.toFixed(2)+" / "+d.gpu.vram_total_gb.toFixed(2)+" ГБ";document.getElementById("vram-bar").style.width=d.gpu.vram_percent+"%";}
  else{document.getElementById("vram").textContent="нет GPU";}
  document.getElementById("disk").textContent=d.disk_used_gb.toFixed(1)+" / "+d.disk_total_gb.toFixed(1)+" ГБ ("+d.disk_percent.toFixed(1)+"%)";
  document.getElementById("disk-bar").style.width=d.disk_percent+"%";
}catch(e){console.error(e);}}
tick();setInterval(tick,3000);
</script></body></html>""")


# ══════════════════════════════════════════════════════════════════
# API профиля
# ══════════════════════════════════════════════════════════════════

@router.get("/api/profile/load")
async def api_profile_load():
    try:
        return JSONResponse(profile_load_all())
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/profile/save")
async def api_profile_save(request: Request):
    try:
        body: dict = await request.json()
        for key, value in body.items():
            profile_set(key, value)
        return {"ok": True, "updated": list(body.keys())}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/profile/reset")
async def api_profile_reset():
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM user_profile")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════
# API истории команд
# ══════════════════════════════════════════════════════════════════

@router.get("/api/profile/history")
async def api_history_load(limit: int = 200):
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT command_name, raw_text, confidence, ok, executed_at
                   FROM local_command_history
                   ORDER BY executed_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return JSONResponse([{
            "cmd": r["raw_text"],
            "name": r["command_name"],
            "conf": round(r["confidence"], 2),
            "ok": bool(r["ok"]),
            "ts": r["executed_at"][:16].replace("T", " "),
        } for r in rows])
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/profile/history/add")
async def api_history_add(entry: CommandHistoryEntry):
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO local_command_history
                   (command_name, raw_text, params_json, confidence, ok, error_msg)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry.command_name, entry.raw_text,
                 json.dumps(entry.params, ensure_ascii=False),
                 entry.confidence, int(entry.ok), entry.error_msg),
            )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/profile/clear_history")
async def api_history_clear():
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM local_command_history")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════
# API диалогов (conversation_history)
# ══════════════════════════════════════════════════════════════════

@router.get("/api/conversations")
async def api_conversations(session_id: Optional[str] = None, limit: int = 50):
    try:
        with get_db() as conn:
            if session_id:
                rows = conn.execute(
                    """SELECT session_id, role, text, emotion, agent, timestamp
                       FROM conversation_history WHERE session_id=?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT session_id, role, text, emotion, agent, timestamp
                       FROM conversation_history ORDER BY timestamp DESC LIMIT ?""",
                    (limit,),
                ).fetchall()

        # Дешифруем если сообщения зашифрованы
        from core.encryption import decrypt_if_needed
        return JSONResponse([{
            "session_id": r["session_id"],
            "role": r["role"],
            "text": decrypt_if_needed(r["text"] or ""),
            "emotion": r["emotion"],
            "agent": r["agent"],
            "timestamp": r["timestamp"],
        } for r in rows])
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/conversations/clear")
async def api_conversations_clear(session_id: Optional[str] = None):
    try:
        with get_db() as conn:
            if session_id:
                conn.execute("DELETE FROM conversation_history WHERE session_id=?",
                             (session_id,))
            else:
                conn.execute("DELETE FROM conversation_history")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════
# API мониторинга системы (CPU / RAM / VRAM / Disk)
# ══════════════════════════════════════════════════════════════════

@router.get("/api/system/stats")
async def api_system_stats():
    result: dict = {
        "cpu_percent": 0.0,
        "ram_total_gb": 0.0, "ram_used_gb": 0.0, "ram_percent": 0.0,
        "disk_total_gb": 0.0, "disk_used_gb": 0.0, "disk_percent": 0.0,
        "gpu": None,
    }
    try:
        import psutil
        result["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        result["ram_total_gb"] = ram.total / (1024 ** 3)
        result["ram_used_gb"] = ram.used / (1024 ** 3)
        result["ram_percent"] = ram.percent
        disk = psutil.disk_usage("/")
        result["disk_total_gb"] = disk.total / (1024 ** 3)
        result["disk_used_gb"] = disk.used / (1024 ** 3)
        result["disk_percent"] = disk.percent
    except ImportError:
        result["warning"] = "psutil не установлен"
    except Exception as e:
        result["error"] = str(e)

    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        result["gpu"] = {
            "vram_total_gb": mem.total / (1024 ** 3),
            "vram_used_gb": mem.used / (1024 ** 3),
            "vram_percent": (mem.used / mem.total * 100) if mem.total else 0,
            "gpu_percent": util.gpu,
            "name": pynvml.nvmlDeviceGetName(h).decode() if isinstance(
                pynvml.nvmlDeviceGetName(h), bytes) else pynvml.nvmlDeviceGetName(h),
        }
        pynvml.nvmlShutdown()
    except Exception:
        pass  # GPU может отсутствовать

    return JSONResponse(result)


# ══════════════════════════════════════════════════════════════════
# API двухфакторной аутентификации (TOTP)
# ══════════════════════════════════════════════════════════════════

@router.post("/api/2fa/setup")
async def api_2fa_setup():
    """Генерирует новый TOTP-секрет и возвращает QR-код (base64 png)."""
    try:
        import pyotp
        import qrcode
        import io
        import base64
    except ImportError:
        raise HTTPException(500, "pyotp / qrcode не установлены. "
                                 "pip install pyotp qrcode[pil]")

    secret = pyotp.random_base32()
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO two_factor (id, secret, enabled) VALUES (1, ?, 0)",
            (secret,),
        )

    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name="admin", issuer_name="Сиен 3.0")
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return {"secret": secret, "uri": uri, "qr_png_base64": qr_b64}


@router.post("/api/2fa/verify")
async def api_2fa_verify(req: TOTPVerify):
    try:
        import pyotp
    except ImportError:
        raise HTTPException(500, "pyotp не установлен")

    with get_db() as conn:
        row = conn.execute("SELECT secret FROM two_factor WHERE id=1").fetchone()
    if not row:
        raise HTTPException(404, "2FA не настроена")

    totp = pyotp.TOTP(row["secret"])
    if not totp.verify(req.code, valid_window=1):
        raise HTTPException(401, "Неверный код")

    with get_db() as conn:
        conn.execute("UPDATE two_factor SET enabled=1 WHERE id=1")
    return {"ok": True, "enabled": True}


@router.get("/api/2fa/status")
async def api_2fa_status():
    with get_db() as conn:
        row = conn.execute("SELECT enabled FROM two_factor WHERE id=1").fetchone()
    return {"enabled": bool(row["enabled"]) if row else False}


@router.post("/api/2fa/disable")
async def api_2fa_disable():
    with get_db() as conn:
        conn.execute("DELETE FROM two_factor WHERE id=1")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# Быстрое выполнение команды
# ══════════════════════════════════════════════════════════════════

@router.post("/api/command")
async def api_run_command(request: Request):
    try:
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            raise HTTPException(400, "Пустая команда")

        try:
            # Правильный импорт
            from core.local_commands import match_command
            cmd_name, params, conf = match_command(text)
        except ImportError:
            cmd_name, params, conf = None, {}, 0.0

        return {
            "text": text,
            "command_name": cmd_name,
            "params": params,
            "confidence": conf,
            "handled": cmd_name is not None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════
# Инициализация
# ══════════════════════════════════════════════════════════════════

try:
    init_profile_db()
except Exception as _e:
    logger.warning(f"init_profile_db: {_e}")
