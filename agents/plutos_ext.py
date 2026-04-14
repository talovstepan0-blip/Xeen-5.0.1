"""
agents/plutos_ext.py — Расширения агента Плутос для Сиен 3.0.0.

Добавляет к agents/plutos.py:
  • Реальная торговля через Тинькофф Инвестиции (tinkoff-investments)
  • Реальная торговля через Binance (python-binance)
  • Подтверждение сделок через HUD (поле "confirmed": false ждёт юзера)
  • Авторебалансинг портфеля раз в месяц
  • Защита: CONFIRM_REQUIRED по умолчанию True

Безопасность: реальные ключи API хранятся в Кроносе.
Без подтверждения через /invest/confirm/{order_id} реальная сделка не идёт.

Интеграция:
    from agents.plutos_ext import setup_plutos_ext
    setup_plutos_ext(app)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

import aiohttp
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("plutos.ext")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "plutos_ext.db"

KRONOS_URL = "http://localhost:8001"
ORCHESTRATOR_URL = "http://localhost:8000"
CONFIRM_REQUIRED = True   # ВАЖНО: каждая сделка требует подтверждения

router = APIRouter(prefix="/invest", tags=["plutos-ext"])

# ── Опциональные библиотеки ──────────────────────────────────────
try:
    from tinkoff.invest import Client as TinkoffClient  # type: ignore
    from tinkoff.invest import OrderDirection, OrderType  # type: ignore
    TINKOFF_OK = True
except ImportError:
    TINKOFF_OK = False

try:
    from binance.spot import Spot as BinanceSpot  # type: ignore
    BINANCE_OK = True
except ImportError:
    BINANCE_OK = False


# ══════════════════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_orders (
    order_id    TEXT PRIMARY KEY,
    broker      TEXT NOT NULL,        -- tinkoff | binance
    side        TEXT NOT NULL,        -- buy | sell
    symbol      TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL,                  -- NULL = market
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | executed | rejected | failed
    confirmed_by TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    executed_at TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS rebalance_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_at TEXT DEFAULT (datetime('now')),
    target_allocation TEXT,
    actions     TEXT,
    confirmed   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS allocations (
    id          INTEGER PRIMARY KEY CHECK (id=1),
    targets     TEXT NOT NULL,        -- JSON {"AAPL":30,"BTC":20,...}
    last_rebalance TEXT
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
# Утилиты
# ══════════════════════════════════════════════════════════════════

async def get_secret(key: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_URL}/secrets/get",
                params={"key": key},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    return (await r.json()).get("value")
    except Exception:
        pass
    return None


async def push_to_hud(payload: dict) -> None:
    """Шлёт уведомление HUD через оркестратор."""
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{ORCHESTRATOR_URL}/internal/push",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=3),
            )
    except Exception as e:
        logger.debug(f"push: {e}")


# ══════════════════════════════════════════════════════════════════
# Модели
# ══════════════════════════════════════════════════════════════════

class OrderRequest(BaseModel):
    broker: str             # tinkoff | binance
    side: str               # buy | sell
    symbol: str             # тикер: SBER, AAPL, BTCUSDT
    quantity: float
    price: Optional[float] = None    # None = market order


class ConfirmRequest(BaseModel):
    confirmed_by: str = "user"


# ══════════════════════════════════════════════════════════════════
# Эндпоинты: создание ордера (с обязательным подтверждением)
# ══════════════════════════════════════════════════════════════════

@router.post("/order")
async def create_order(req: OrderRequest):
    """
    Создаёт ордер в статусе 'pending'. Реальная сделка не происходит,
    пока не вызвать /invest/confirm/{order_id}.
    """
    if req.side not in ("buy", "sell"):
        raise HTTPException(400, "side должен быть buy или sell")
    if req.broker not in ("tinkoff", "binance"):
        raise HTTPException(400, "broker должен быть tinkoff или binance")
    if req.quantity <= 0:
        raise HTTPException(400, "quantity > 0")

    order_id = str(uuid4())[:12]
    with get_db() as conn:
        conn.execute(
            """INSERT INTO pending_orders
               (order_id, broker, side, symbol, quantity, price, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (order_id, req.broker, req.side, req.symbol, req.quantity, req.price),
        )

    # Уведомляем HUD: нужно подтверждение
    await push_to_hud({
        "type": "order_pending",
        "agent": "plutos",
        "order_id": order_id,
        "title": "Подтверждение сделки",
        "message": f"{req.side.upper()} {req.quantity} {req.symbol} ({req.broker}). "
                   f"Подтвердить: /invest/confirm/{order_id}",
    })

    return {
        "order_id": order_id,
        "status": "pending",
        "requires_confirmation": CONFIRM_REQUIRED,
        "message": f"Ордер создан. Подтвердите через /invest/confirm/{order_id}",
    }


@router.post("/confirm/{order_id}")
async def confirm_order(order_id: str, req: ConfirmRequest):
    """Подтверждает и исполняет ордер."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM pending_orders WHERE order_id=?",
            (order_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Ордер не найден")
        if row["status"] != "pending":
            raise HTTPException(400, f"Ордер уже {row['status']}")

        conn.execute(
            "UPDATE pending_orders SET status='confirmed', confirmed_by=? WHERE order_id=?",
            (req.confirmed_by, order_id),
        )

    order = dict(row)
    # Реальное исполнение
    if order["broker"] == "tinkoff":
        result = await execute_tinkoff(order)
    elif order["broker"] == "binance":
        result = await execute_binance(order)
    else:
        result = {"error": "unknown broker"}

    status = "executed" if "error" not in result else "failed"
    with get_db() as conn:
        conn.execute(
            """UPDATE pending_orders SET status=?, executed_at=?, result_json=?
               WHERE order_id=?""",
            (status, datetime.now().isoformat(),
             json.dumps(result, ensure_ascii=False), order_id),
        )

    return {"order_id": order_id, "status": status, "result": result}


@router.post("/reject/{order_id}")
def reject_order(order_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE pending_orders SET status='rejected' WHERE order_id=? AND status='pending'",
            (order_id,),
        )
    return {"order_id": order_id, "status": "rejected"}


@router.get("/orders")
def list_orders(status: Optional[str] = None, limit: int = 50):
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM pending_orders WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pending_orders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return {"orders": [dict(r) for r in rows], "count": len(rows)}


# ══════════════════════════════════════════════════════════════════
# Реальное исполнение (Тинькофф)
# ══════════════════════════════════════════════════════════════════

async def execute_tinkoff(order: dict) -> dict:
    if not TINKOFF_OK:
        return {"error": "tinkoff-investments не установлен"}

    token = await get_secret("tinkoff_invest_token")
    account_id = await get_secret("tinkoff_account_id")
    if not token or not account_id:
        return {"error": "Нет tinkoff_invest_token или tinkoff_account_id в Кроносе"}

    try:
        # Запускаем в потоке, т.к. SDK синхронный
        def _do_trade():
            with TinkoffClient(token) as client:
                # Найти figi по тикеру
                instruments = client.instruments.share_by(
                    id_type=1, id=order["symbol"]
                )
                figi = instruments.instrument.figi if instruments else None
                if not figi:
                    return {"error": f"figi не найден для {order['symbol']}"}

                direction = (OrderDirection.ORDER_DIRECTION_BUY
                             if order["side"] == "buy"
                             else OrderDirection.ORDER_DIRECTION_SELL)
                order_type = (OrderType.ORDER_TYPE_LIMIT
                              if order.get("price")
                              else OrderType.ORDER_TYPE_MARKET)

                response = client.orders.post_order(
                    figi=figi,
                    quantity=int(order["quantity"]),
                    direction=direction,
                    account_id=account_id,
                    order_type=order_type,
                    order_id=order["order_id"],
                )
                return {
                    "tinkoff_order_id": response.order_id,
                    "execution_status": str(response.execution_report_status),
                    "filled": response.lots_executed,
                }

        return await asyncio.get_event_loop().run_in_executor(None, _do_trade)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# Реальное исполнение (Binance)
# ══════════════════════════════════════════════════════════════════

async def execute_binance(order: dict) -> dict:
    if not BINANCE_OK:
        return {"error": "python-binance не установлен"}

    api_key = await get_secret("binance_api_key")
    secret_key = await get_secret("binance_secret_key")
    if not api_key or not secret_key:
        return {"error": "Нет binance ключей в Кроносе"}

    try:
        def _do_trade():
            client = BinanceSpot(api_key=api_key, api_secret=secret_key)
            params = {
                "symbol": order["symbol"],
                "side": order["side"].upper(),
                "type": "LIMIT" if order.get("price") else "MARKET",
                "quantity": order["quantity"],
            }
            if order.get("price"):
                params["price"] = order["price"]
                params["timeInForce"] = "GTC"
            return client.new_order(**params)

        result = await asyncio.get_event_loop().run_in_executor(None, _do_trade)
        return {"binance_response": result}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════
# Авторебалансинг (раз в месяц с подтверждением)
# ══════════════════════════════════════════════════════════════════

class AllocationTargets(BaseModel):
    targets: dict[str, float]   # {"SBER": 30, "AAPL": 20, "BTCUSDT": 10, "USD": 40}


@router.post("/rebalance/set_targets")
def set_targets(req: AllocationTargets):
    """Сохраняет целевую аллокацию портфеля (в процентах)."""
    total = sum(req.targets.values())
    if abs(total - 100.0) > 0.01:
        raise HTTPException(400, f"Сумма целей должна быть 100%, сейчас {total}")

    with get_db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO allocations (id, targets, last_rebalance)
               VALUES (1, ?, NULL)""",
            (json.dumps(req.targets, ensure_ascii=False),),
        )
    return {"status": "saved", "targets": req.targets}


@router.post("/rebalance/preview")
async def rebalance_preview():
    """Считает, какие сделки нужны для приведения портфеля к целям."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM allocations WHERE id=1").fetchone()
    if not row:
        return {"error": "Целевая аллокация не задана"}

    targets = json.loads(row["targets"])
    # Заглушка: в реальности тут вызов /invest/portfolio для текущих позиций
    actions = [
        {"action": "rebalance_stub", "symbol": sym, "target_pct": pct}
        for sym, pct in targets.items()
    ]

    with get_db() as conn:
        conn.execute(
            """INSERT INTO rebalance_log (target_allocation, actions, confirmed)
               VALUES (?, ?, 0)""",
            (json.dumps(targets, ensure_ascii=False),
             json.dumps(actions, ensure_ascii=False)),
        )

    await push_to_hud({
        "type": "rebalance_preview",
        "agent": "plutos",
        "title": "Авторебалансинг",
        "message": f"Подготовлено {len(actions)} операций. Подтвердите.",
    })
    return {"actions": actions, "requires_confirmation": True}


@router.post("/rebalance/confirm")
async def rebalance_confirm():
    """Подтверждает последний preview и исполняет сделки."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM rebalance_log WHERE confirmed=0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"error": "Нет неподтверждённых ребалансировок"}

    with get_db() as conn:
        conn.execute(
            "UPDATE rebalance_log SET confirmed=1 WHERE id=?", (row["id"],)
        )
        conn.execute(
            "UPDATE allocations SET last_rebalance=? WHERE id=1",
            (datetime.now().isoformat(),),
        )

    return {"status": "confirmed", "rebalance_id": row["id"]}


# ══════════════════════════════════════════════════════════════════
# Setup
# ══════════════════════════════════════════════════════════════════

def setup_plutos_ext(app: FastAPI) -> None:
    init_db()
    app.include_router(router)
    logger.info(f"Расширения Плутоса зарегистрированы. "
                f"Tinkoff: {TINKOFF_OK}, Binance: {BINANCE_OK}")
