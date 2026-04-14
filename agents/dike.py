"""
agents/dike.py — Агент Дике (Бухгалтер). Сиен 3.0.0.

Изменения: добавлен app = FastAPI(), абсолютный путь к БД, health-эндпоинт,
контекстный менеджер для sqlite.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("dike")
logging.basicConfig(level=logging.INFO, format="[ДИКЕ] %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dike.db"

router = APIRouter(prefix="/accounting", tags=["dike"])

DISTRIBUTION_RULES = {"development": 40.0, "reserve": 30.0, "investor": 30.0}


# ══════════════════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS income (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    amount      REAL NOT NULL,
    currency    TEXT DEFAULT 'RUB',
    description TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    amount      REAL NOT NULL,
    currency    TEXT DEFAULT 'RUB',
    description TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS distributions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    total_profit   REAL NOT NULL,
    development    REAL NOT NULL,
    reserve        REAL NOT NULL,
    investor       REAL NOT NULL,
    distributed_at TEXT NOT NULL
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
# Модели
# ══════════════════════════════════════════════════════════════════

class IncomeIn(BaseModel):
    source: str
    amount: float
    currency: str = "RUB"
    description: Optional[str] = None


class ExpenseIn(BaseModel):
    category: str
    amount: float
    currency: str = "RUB"
    description: Optional[str] = None


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

@router.post("/income")
def add_income(req: IncomeIn):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO income (source, amount, currency, description, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (req.source, req.amount, req.currency, req.description,
             datetime.now().isoformat()),
        )
    return {"status": "added", "source": req.source, "amount": req.amount}


@router.post("/expense")
def add_expense(req: ExpenseIn):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO expenses (category, amount, currency, description, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (req.category, req.amount, req.currency, req.description,
             datetime.now().isoformat()),
        )
    return {"status": "added", "category": req.category, "amount": req.amount}


@router.post("/distribute")
def distribute():
    """Распределить текущую прибыль по правилам."""
    with get_db() as conn:
        income = conn.execute("SELECT COALESCE(SUM(amount),0) FROM income").fetchone()[0]
        expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
        profit = income - expenses
        if profit <= 0:
            raise HTTPException(400, f"Прибыль отсутствует: {profit}")

        dev = round(profit * DISTRIBUTION_RULES["development"] / 100, 2)
        res = round(profit * DISTRIBUTION_RULES["reserve"] / 100, 2)
        inv = round(profit * DISTRIBUTION_RULES["investor"] / 100, 2)

        conn.execute(
            """INSERT INTO distributions
               (total_profit, development, reserve, investor, distributed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (profit, dev, res, inv, datetime.now().isoformat()),
        )
    return {
        "total_profit": profit,
        "development": dev,
        "reserve": res,
        "investor": inv,
    }


@router.get("/summary")
def summary():
    with get_db() as conn:
        income = conn.execute("SELECT COALESCE(SUM(amount),0) FROM income").fetchone()[0]
        expenses = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
        by_source = conn.execute(
            "SELECT source, SUM(amount) AS total FROM income GROUP BY source"
        ).fetchall()
        by_cat = conn.execute(
            "SELECT category, SUM(amount) AS total FROM expenses GROUP BY category"
        ).fetchall()
    return {
        "income_total": round(income, 2),
        "expenses_total": round(expenses, 2),
        "profit": round(income - expenses, 2),
        "income_by_source": {r["source"]: r["total"] for r in by_source},
        "expenses_by_category": {r["category"]: r["total"] for r in by_cat},
        "distribution_rules": DISTRIBUTION_RULES,
    }


@router.get("/distribution_history")
def distribution_history():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT total_profit, development, reserve, investor, distributed_at
               FROM distributions ORDER BY distributed_at DESC LIMIT 20"""
        ).fetchall()
    return {"history": [dict(r) for r in rows]}


@router.get("/health")
def health():
    return {"agent": "Дике", "alive": True, "rules": DISTRIBUTION_RULES}


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

init_db()
app = FastAPI(title="Дике — Бухгалтер")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8020)
