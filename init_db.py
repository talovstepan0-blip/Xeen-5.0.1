"""init_db.py — создаёт data/sien.db и базовые таблицы. Запуск: python init_db.py"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
(BASE / "logs").mkdir(exist_ok=True)
(BASE / "backups").mkdir(exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    level     TEXT NOT NULL DEFAULT 'INFO',
    source    TEXT NOT NULL,
    message   TEXT NOT NULL,
    ts        REAL NOT NULL DEFAULT (unixepoch('now'))
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source);

CREATE TABLE IF NOT EXISTS conversation_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    emotion    TEXT,
    agent      TEXT,
    timestamp  REAL    NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_ch_session_ts ON conversation_history(session_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS user_profile (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
CREATE INDEX IF NOT EXISTS idx_cmd_history_ts ON local_command_history(executed_at DESC);

CREATE TABLE IF NOT EXISTS two_factor (
    id         INTEGER PRIMARY KEY CHECK (id=1),
    secret     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO user_profile (key, value) VALUES
    ('username', '"Пользователь"'),
    ('city',     '"Riga"'),
    ('theme',    '"cyberpunk"'),
    ('hotword',  '"Сиен"');
"""

db = DATA / "sien.db"
with sqlite3.connect(str(db)) as conn:
    conn.executescript(SCHEMA)
print(f"✓ БД создана: {db}")
print("✓ Папки data/, logs/, backups/ готовы")
print("Можно запускать: python start.py")
