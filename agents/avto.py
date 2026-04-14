"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Авто' (Макросы) — запись и воспроизведение действий мыши/клавиатуры.
Этап 4 проекта 'Сиен 01'.

ВНИМАНИЕ: pynput требует графического окружения (не работает в headless/Docker).
На сервере используй заглушку-режим (AVTO_HEADLESS=1).
Установи: pip install pynput
"""

import os, sqlite3, logging, json, threading, time
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("avto")
logging.basicConfig(level=logging.INFO, format="[АВТО] %(message)s")

router   = APIRouter(prefix="/macro", tags=["avto"])
DB_PATH  = str(_DATA_DIR / "avto.db")
HEADLESS = os.environ.get("AVTO_HEADLESS", "0") == "1"

Path("data").mkdir(exist_ok=True)

# ── pynput с graceful degradation ─────────────────────────────────────────────
try:
    from pynput import mouse, keyboard
    from pynput.mouse    import Button, Controller as MouseCtrl
    from pynput.keyboard import Key, Controller as KeyCtrl
    PYNPUT_OK = not HEADLESS
    if HEADLESS:
        logger.warning("AVTO_HEADLESS=1 — pynput отключён")
    else:
        logger.info("pynput загружен")
except ImportError:
    PYNPUT_OK = False
    logger.warning("pynput не установлен. Установи: pip install pynput")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS macros (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT UNIQUE NOT NULL,
            description TEXT,
            events     TEXT NOT NULL,     -- JSON список событий
            created_at TEXT DEFAULT (datetime('now')),
            run_count  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS runs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            macro_id   INTEGER,
            status     TEXT,
            ran_at     TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit(); conn.close()

init_db()

# ── Состояние записи ──────────────────────────────────────────────────────────
_recording       = False
_recorded_events = []
_record_lock     = threading.Lock()
_listeners       = []

def _on_mouse_click(x, y, button, pressed):
    if _recording and pressed:
        with _record_lock:
            _recorded_events.append({
                "type": "mouse_click", "x": x, "y": y,
                "button": str(button), "ts": time.time()
            })

def _on_key_press(key):
    if _recording:
        with _record_lock:
            try:
                char = key.char
            except AttributeError:
                char = str(key)
            _recorded_events.append({"type": "key_press", "key": char, "ts": time.time()})

# ── Модели ────────────────────────────────────────────────────────────────────

class SaveMacroReq(BaseModel):
    name: str
    description: Optional[str] = None

class CreateMacroReq(BaseModel):
    name: str
    description: Optional[str] = None
    events: list[dict]   # ручное создание макроса

class RunOptions(BaseModel):
    speed: float = 1.0    # множитель скорости (0.5 = вдвое быстрее)

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/start_recording")
def start_recording():
    """Начать запись действий мыши и клавиатуры."""
    global _recording, _recorded_events, _listeners

    if not PYNPUT_OK:
        return {"status": "stub", "note": "pynput недоступен. Установи pip install pynput (требует GUI)"}

    if _recording:
        raise HTTPException(400, "Запись уже идёт")

    _recorded_events = []
    _recording = True

    m_listener = mouse.Listener(on_click=_on_mouse_click)
    k_listener = keyboard.Listener(on_press=_on_key_press)
    _listeners  = [m_listener, k_listener]
    m_listener.start(); k_listener.start()

    logger.info("Запись макроса начата")
    return {"status": "recording", "message": "Запись началась. Выполни действия, затем вызови /macro/stop_recording"}

@router.post("/stop_recording")
def stop_recording(req: SaveMacroReq):
    """Остановить запись и сохранить макрос."""
    global _recording, _listeners

    if not PYNPUT_OK:
        # Заглушка: сохраняем тестовый макрос
        events = [
            {"type": "key_press", "key": "h", "ts": time.time()},
            {"type": "key_press", "key": "i", "ts": time.time() + 0.1},
        ]
        _save_macro(req.name, req.description, events)
        return {"status": "stub_saved", "name": req.name, "events": len(events)}

    if not _recording:
        raise HTTPException(400, "Запись не идёт")

    _recording = False
    for listener in _listeners:
        listener.stop()
    _listeners = []

    with _record_lock:
        events = list(_recorded_events)

    if not events:
        raise HTTPException(400, "Не записано ни одного события")

    _save_macro(req.name, req.description, events)
    logger.info(f"Макрос '{req.name}' сохранён: {len(events)} событий")
    return {"status": "saved", "name": req.name, "events_count": len(events)}

def _save_macro(name: str, description: Optional[str], events: list):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            INSERT INTO macros (name, description, events) VALUES (?,?,?)
            ON CONFLICT(name) DO UPDATE SET events=excluded.events, description=excluded.description
        """, (name, description, json.dumps(events)))
        conn.commit()
    finally:
        conn.close()

@router.post("/run/{name}")
def run_macro(name: str, options: Optional[RunOptions] = None):
    """Воспроизвести записанный макрос."""
    speed = (options.speed if options else 1.0)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM macros WHERE name=?", (name,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Макрос '{name}' не найден")

    events   = json.loads(row["events"])
    macro_id = row["id"]
    conn.execute("UPDATE macros SET run_count=run_count+1 WHERE id=?", (macro_id,))
    conn.commit(); conn.close()

    if not PYNPUT_OK:
        logger.info(f"[ЗАГЛУШКА] Воспроизведение '{name}': {len(events)} событий")
        return {"status": "stub_run", "macro": name, "events": len(events), "note": "Требуется pynput и GUI"}

    # Реальное воспроизведение
    m_ctrl = MouseCtrl()
    k_ctrl = KeyCtrl()

    def _run():
        prev_ts = None
        for event in events:
            if prev_ts is not None:
                delay = (event["ts"] - prev_ts) / speed
                time.sleep(max(0, min(delay, 2.0)))  # не ждём > 2 сек
            prev_ts = event["ts"]

            if event["type"] == "mouse_click":
                m_ctrl.position = (event["x"], event["y"])
                m_ctrl.click(Button.left)
            elif event["type"] == "key_press":
                try:
                    k_ctrl.press(event["key"])
                    k_ctrl.release(event["key"])
                except Exception:
                    pass

        conn2 = sqlite3.connect(DB_PATH)
        conn2.execute("INSERT INTO runs (macro_id, status) VALUES (?,?)", (macro_id, "completed"))
        conn2.commit(); conn2.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    logger.info(f"Макрос '{name}' запущен: {len(events)} событий")
    return {"status": "running", "macro": name, "events": len(events)}

@router.post("/create")
def create_manual(req: CreateMacroReq):
    """Создать макрос вручную (без записи)."""
    _save_macro(req.name, req.description, req.events)
    return {"status": "created", "name": req.name, "events": len(req.events)}

@router.get("/list")
def list_macros():
    """Список сохранённых макросов."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, description, run_count, created_at FROM macros ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return {"macros": [dict(r) for r in rows]}

@router.get("/{name}")
def get_macro(name: str):
    """Получить макрос с событиями."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM macros WHERE name=?", (name,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Макрос '{name}' не найден")
    d = dict(row)
    d["events"] = json.loads(d["events"])
    return d

@router.delete("/{name}")
def delete_macro(name: str):
    """Удалить макрос."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id FROM macros WHERE name=?", (name,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, f"Макрос '{name}' не найден")
    conn.execute("DELETE FROM macros WHERE name=?", (name,))
    conn.commit(); conn.close()
    return {"status": "deleted", "name": name}

@router.get("/status/recording")
def recording_status():
    return {"recording": _recording, "events_so_far": len(_recorded_events), "pynput": PYNPUT_OK}

@router.get("/health")
def health():
    return {"agent": "Авто", "alive": True, "pynput": PYNPUT_OK, "headless": HEADLESS}

app = FastAPI(title="Авто — Макросы")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8013)
