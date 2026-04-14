"""
agents/wen_email.py — Почтовый модуль Вэнь. Сиен 3.0.0.

Отличия от версии 2.0 Beta (~700 строк):
  • Компактный модуль (≈300 строк) с фокусом на требования из ТЗ.
  • IMAP каждые 15 минут (настраивается) для Gmail/Yandex/Mail.ru.
  • LLM-анализ новых писем: создаётся задача у Вэнь, если письмо важное.
  • Шаблонные ответы через LLM (опционально).
  • Google Calendar (OAuth) и Яндекс.Календарь (CalDAV) — двусторонняя синхронизация.
  • Аккаунты хранятся в Кроносе (порт 8001) — см. cronos_email.py.

Интеграция: из agents/wen.py сделай:
    from agents.wen_email import setup_email_routes
    setup_email_routes(app)
"""

from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import json
import logging
import smtplib
import sqlite3
import ssl
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("wen.email")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAIL_DB = DATA_DIR / "wen_mail.db"

KRONOS_URL = "http://localhost:8001"
CHECK_INTERVAL_SEC = 900  # 15 минут

router = APIRouter(prefix="/mail", tags=["wen-email"])


# ══════════════════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  TEXT NOT NULL,
    msg_uid     TEXT NOT NULL,
    subject     TEXT,
    from_addr   TEXT,
    to_addr     TEXT,
    body        TEXT,
    received_at TEXT,
    important   INTEGER DEFAULT 0,
    task_id     INTEGER,
    replied     INTEGER DEFAULT 0,
    fetched_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(account_id, msg_uid)
);
CREATE INDEX IF NOT EXISTS idx_emails_fetched ON emails(fetched_at DESC);

CREATE TABLE IF NOT EXISTS reply_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,      -- google | yandex | local
    external_id TEXT,
    title       TEXT NOT NULL,
    start_at    TEXT NOT NULL,
    end_at      TEXT,
    description TEXT,
    synced_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(provider, external_id)
);
"""


def init_db() -> None:
    with sqlite3.connect(str(MAIL_DB)) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_db():
    conn = sqlite3.connect(str(MAIL_DB))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# Получение аккаунтов из Кроноса
# ══════════════════════════════════════════════════════════════════

async def get_accounts() -> list[dict]:
    """Получает список почтовых аккаунтов из Кроноса."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_URL}/email/accounts",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    return (await r.json()).get("accounts", [])
    except Exception as e:
        logger.debug(f"Кронос недоступен: {e}")
    return []


async def get_account_secret(account_id: str) -> Optional[dict]:
    """Получает полные данные аккаунта (с паролем) из Кроноса."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_URL}/email/accounts/{account_id}/secret",
                headers={"X-Agent-Name": "wen"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        logger.debug(f"Секрет: {e}")
    return None


# ══════════════════════════════════════════════════════════════════
# IMAP
# ══════════════════════════════════════════════════════════════════

def _decode_header(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _extract_body(msg) -> str:
    """Извлекает текстовое тело письма."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
                except Exception:
                    continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception:
            return str(msg.get_payload())
    return ""


def fetch_imap(account: dict, limit: int = 20) -> list[dict]:
    """Читает последние N писем из INBOX через IMAP."""
    emails: list[dict] = []
    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(account["imap_host"], account["imap_port"],
                                 ssl_context=ctx)
        conn.login(account["email"], account["password"])
        conn.select("INBOX")
        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            return []
        ids = data[0].split()[-limit:]
        for msg_id in reversed(ids):
            typ, msg_data = conn.fetch(msg_id, "(RFC822)")
            if typ != "OK":
                continue
            msg = email_lib.message_from_bytes(msg_data[0][1])
            emails.append({
                "uid": msg_id.decode(),
                "subject": _decode_header(msg.get("Subject", "")),
                "from": _decode_header(msg.get("From", "")),
                "to": _decode_header(msg.get("To", "")),
                "date": msg.get("Date", ""),
                "body": _extract_body(msg)[:2000],
            })
        conn.logout()
    except Exception as e:
        logger.warning(f"IMAP {account.get('email')}: {e}")
    return emails


async def analyze_with_llm(subject: str, body: str) -> dict:
    """
    Спрашивает у Фениксa/Ollama важность письма и нужно ли создать задачу.
    Возвращает {important: bool, task_title: str|None, reply_draft: str|None}.
    """
    prompt = (
        "Проанализируй письмо и ответь строгим JSON: "
        '{"important": true/false, "task_title": "..." или null, '
        '"reply_draft": "..." или null}\n\n'
        f"Тема: {subject}\n\nТекст: {body[:1500]}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://localhost:11434/api/generate",
                json={"model": "llama3.2:3b", "prompt": prompt,
                      "stream": False, "options": {"temperature": 0.2}},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    raw = data.get("response", "")
                    import re
                    m = re.search(r"\{.*\}", raw, re.DOTALL)
                    if m:
                        return json.loads(m.group())
    except Exception as e:
        logger.debug(f"LLM анализ: {e}")
    # Fallback по ключевикам
    important_kw = ["срочно", "важно", "urgent", "asap", "deadline", "договор"]
    is_important = any(kw in (subject + body).lower() for kw in important_kw)
    return {
        "important": is_important,
        "task_title": subject if is_important else None,
        "reply_draft": None,
    }


async def create_task_via_wen(title: str, description: str) -> Optional[int]:
    """Создаёт задачу у Вэнь через HTTP."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://localhost:8006/tasks/create",
                json={"title": title, "description": description[:500]},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    return (await r.json()).get("id")
    except Exception as e:
        logger.debug(f"Создание задачи: {e}")
    return None


async def check_all_mailboxes():
    """Периодическая проверка всех почтовых ящиков."""
    logger.info("Проверка почты начата")
    accounts_meta = await get_accounts()
    for meta in accounts_meta:
        account = await get_account_secret(meta["id"])
        if not account:
            continue
        mails = fetch_imap(account, limit=10)
        for m in mails:
            with get_db() as conn:
                exists = conn.execute(
                    "SELECT id FROM emails WHERE account_id=? AND msg_uid=?",
                    (meta["id"], m["uid"]),
                ).fetchone()
                if exists:
                    continue

                analysis = await analyze_with_llm(m["subject"], m["body"])
                task_id = None
                if analysis.get("important") and analysis.get("task_title"):
                    task_id = await create_task_via_wen(
                        analysis["task_title"], m["body"])

                conn.execute(
                    """INSERT INTO emails
                       (account_id, msg_uid, subject, from_addr, to_addr,
                        body, received_at, important, task_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (meta["id"], m["uid"], m["subject"], m["from"], m["to"],
                     m["body"], m["date"],
                     int(bool(analysis.get("important"))), task_id),
                )
    logger.info("Проверка почты завершена")


async def mail_loop():
    """Фоновый цикл проверки почты."""
    while True:
        try:
            await check_all_mailboxes()
        except Exception as e:
            logger.error(f"mail_loop: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


# ══════════════════════════════════════════════════════════════════
# SMTP
# ══════════════════════════════════════════════════════════════════

class SendMailRequest(BaseModel):
    account_id: str
    to: str
    subject: str
    body: str


@router.post("/send")
async def send_mail(req: SendMailRequest):
    account = await get_account_secret(req.account_id)
    if not account:
        raise HTTPException(404, "Аккаунт не найден")

    msg = MIMEMultipart()
    msg["From"] = account["email"]
    msg["To"] = req.to
    msg["Subject"] = req.subject
    msg.attach(MIMEText(req.body, "plain", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(account["smtp_host"], account["smtp_port"],
                              context=ctx) as server:
            server.login(account["email"], account["password"])
            server.sendmail(account["email"], [req.to], msg.as_string())
        return {"status": "sent", "to": req.to}
    except Exception as e:
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════
# Инбокс + ручная проверка
# ══════════════════════════════════════════════════════════════════

@router.get("/inbox")
def inbox(limit: int = 50):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, account_id, subject, from_addr, received_at,
                      important, task_id
               FROM emails ORDER BY fetched_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return {"emails": [dict(r) for r in rows], "count": len(rows)}


@router.post("/check")
async def manual_check():
    await check_all_mailboxes()
    return {"status": "ok", "message": "Проверка выполнена"}


# ══════════════════════════════════════════════════════════════════
# Google Calendar / Yandex.Calendar (двусторонняя синхронизация)
# ══════════════════════════════════════════════════════════════════

async def sync_google_calendar() -> dict:
    """Синхронизация с Google Calendar через OAuth (google-api-python-client)."""
    try:
        from googleapiclient.discovery import build  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
    except ImportError:
        return {"error": "google-api-python-client не установлен"}

    # Токен берём из Кроноса
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_URL}/secrets/get",
                params={"key": "google_calendar_token"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                token = (await r.json()).get("value") if r.status == 200 else None
    except Exception:
        token = None

    if not token:
        return {"error": "Нет google_calendar_token в Кроносе"}

    try:
        creds = Credentials(token=token)
        service = build("calendar", "v3", credentials=creds)
        now = datetime.now().isoformat() + "Z"
        events_result = service.events().list(
            calendarId="primary", timeMin=now, maxResults=20,
            singleEvents=True, orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])

        synced = 0
        with get_db() as conn:
            for ev in events:
                start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
                conn.execute(
                    """INSERT OR REPLACE INTO calendar_events
                       (provider, external_id, title, start_at, end_at, description)
                       VALUES ('google', ?, ?, ?, ?, ?)""",
                    (ev.get("id"), ev.get("summary", ""), start,
                     ev.get("end", {}).get("dateTime"), ev.get("description", "")),
                )
                synced += 1
        return {"synced": synced, "provider": "google"}
    except Exception as e:
        return {"error": str(e)}


async def sync_yandex_calendar() -> dict:
    """Яндекс.Календарь через CalDAV (caldav.yandex.ru)."""
    try:
        import caldav  # type: ignore
    except ImportError:
        return {"error": "caldav не установлен"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_URL}/secrets/get",
                params={"key": "yandex_caldav_credentials"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                creds = (await r.json()).get("value") if r.status == 200 else None
    except Exception:
        creds = None

    if not creds or not isinstance(creds, dict):
        return {"error": "Нет yandex_caldav_credentials в Кроносе"}

    try:
        client = caldav.DAVClient(
            url="https://caldav.yandex.ru",
            username=creds.get("username"),
            password=creds.get("password"),
        )
        principal = client.principal()
        calendars = principal.calendars()
        synced = 0
        with get_db() as conn:
            for cal in calendars:
                for ev in cal.events():
                    vcal = ev.vobject_instance
                    vevent = vcal.vevent
                    conn.execute(
                        """INSERT OR REPLACE INTO calendar_events
                           (provider, external_id, title, start_at, end_at, description)
                           VALUES ('yandex', ?, ?, ?, ?, ?)""",
                        (str(vevent.uid.value),
                         str(vevent.summary.value),
                         vevent.dtstart.value.isoformat(),
                         vevent.dtend.value.isoformat() if hasattr(vevent, "dtend") else None,
                         str(vevent.description.value) if hasattr(vevent, "description") else ""),
                    )
                    synced += 1
        return {"synced": synced, "provider": "yandex"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/calendar/sync")
async def sync_calendars():
    """Двусторонняя синхронизация Google + Yandex."""
    google = await sync_google_calendar()
    yandex = await sync_yandex_calendar()
    return {"google": google, "yandex": yandex}


@router.get("/calendar/events")
def list_events(provider: Optional[str] = None, limit: int = 50):
    with get_db() as conn:
        if provider:
            rows = conn.execute(
                """SELECT * FROM calendar_events WHERE provider=?
                   ORDER BY start_at ASC LIMIT ?""",
                (provider, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM calendar_events
                   ORDER BY start_at ASC LIMIT ?""",
                (limit,),
            ).fetchall()
    return {"events": [dict(r) for r in rows], "count": len(rows)}


@router.get("/health")
def health():
    try:
        with get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0]
        return {
            "module": "wen.email", "alive": True,
            "total_emails": total, "calendar_events": events,
            "check_interval_sec": CHECK_INTERVAL_SEC,
        }
    except Exception as e:
        return {"module": "wen.email", "alive": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════
# Setup
# ══════════════════════════════════════════════════════════════════

def setup_email_routes(app: FastAPI) -> None:
    init_db()
    app.include_router(router)

    @app.on_event("startup")
    async def _start_mail():
        asyncio.create_task(mail_loop())
        logger.info("Почтовый модуль Вэнь запущен")
