"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Плутос' (Инвестор) — анализ рынков, котировки, paper trading.
Этап 4 проекта 'Сиен 01'.

Интеграции: yfinance (акции), ccxt (крипто), Дике (бухгалтер).
"""

import os, sqlite3, logging, json
from datetime import datetime
from typing import Optional
from pathlib import Path
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("plutos")
logging.basicConfig(level=logging.INFO, format="[ПЛУТОС] %(message)s")

router  = APIRouter(prefix="/investor", tags=["plutos"])
DB_PATH = str(_DATA_DIR / "plutos.db")
OLLAMA_URL   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
DIKE_URL     = "http://localhost:8011"

Path("data").mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            quantity   REAL NOT NULL,
            buy_price  REAL NOT NULL,
            bought_at  TEXT DEFAULT (datetime('now')),
            sold_at    TEXT,
            sell_price REAL,
            is_open    INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS deposits (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            amount     REAL NOT NULL,
            source     TEXT DEFAULT 'dike',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS balance (
            id      INTEGER PRIMARY KEY CHECK (id = 1),
            virtual REAL DEFAULT 0.0
        );
        INSERT OR IGNORE INTO balance (id, virtual) VALUES (1, 0.0);
    """)
    conn.commit(); conn.close()

init_db()

# ── Котировки ─────────────────────────────────────────────────────────────────

def get_quote_yfinance(symbol: str) -> dict:
    """
    Котировки акций через yfinance.
    Установи: pip install yfinance
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info   = ticker.info
        hist   = ticker.history(period="5d")
        price  = hist["Close"].iloc[-1] if not hist.empty else info.get("regularMarketPrice", 0)
        return {
            "symbol": symbol.upper(),
            "price":  round(float(price), 4),
            "name":   info.get("longName", symbol),
            "currency": info.get("currency", "USD"),
            "change_pct": round(info.get("regularMarketChangePercent", 0), 2),
            "volume":  info.get("regularMarketVolume", 0),
            "source": "yfinance",
        }
    except ImportError:
        logger.warning("yfinance не установлен. Установи: pip install yfinance")
        return _quote_stub(symbol)
    except Exception as e:
        logger.error(f"yfinance ошибка для {symbol}: {e}")
        return _quote_stub(symbol)

def get_quote_crypto(symbol: str, exchange: str = "binance") -> dict:
    """
    Котировки криптовалюты через ccxt.
    Установи: pip install ccxt
    Поддерживаемые биржи: binance, bybit, okx, kraken и др.
    """
    try:
        import ccxt
        ex    = getattr(ccxt, exchange)()
        pair  = symbol.upper() + "/USDT"
        tick  = ex.fetch_ticker(pair)
        return {
            "symbol": pair,
            "price":  tick["last"],
            "change_pct": round(tick.get("percentage", 0), 2),
            "volume":  tick.get("quoteVolume", 0),
            "source": f"ccxt/{exchange}",
        }
    except ImportError:
        logger.warning("ccxt не установлен. Установи: pip install ccxt")
        return _quote_stub(symbol)
    except Exception as e:
        logger.error(f"ccxt ошибка для {symbol}: {e}")
        return _quote_stub(symbol)

def _quote_stub(symbol: str) -> dict:
    """Заглушка котировки."""
    import random
    return {
        "symbol": symbol.upper(), "price": round(random.uniform(10, 500), 2),
        "change_pct": round(random.uniform(-5, 5), 2),
        "source": "stub", "note": "Заглушка. Установи yfinance или ccxt."
    }

async def llm_recommend(portfolio_summary: str, free_balance: float, query: str = "") -> str:
    prompt = (
        f"Ты инвестиционный советник. Анализируй строго в образовательных целях.\n"
        f"Свободный капитал: {free_balance:.2f} USD (виртуальный)\n"
        f"Портфель:\n{portfolio_summary}\n"
        f"{'Вопрос: ' + query if query else ''}\n\n"
        "Дай рекомендации по диверсификации и управлению рисками. "
        "ПРЕДУПРЕЖДЕНИЕ: это не финансовый совет."
    )
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_URL}/api/generate",
                             json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False})
            return r.json().get("response", "").strip()
    except Exception as e:
        return f"[Ollama недоступна: {e}]"

# ── Модели ────────────────────────────────────────────────────────────────────

class DepositReq(BaseModel):
    amount: float
    source: str = "dike"

class TradeReq(BaseModel):
    symbol: str
    quantity: float
    action: str       # buy | sell
    is_crypto: bool = False
    exchange: str = "binance"

class RecommendReq(BaseModel):
    query: Optional[str] = None

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.get("/quote/{symbol}")
def quote(symbol: str, crypto: bool = False, exchange: str = "binance"):
    """Получить котировку. ?crypto=true для крипто."""
    if crypto:
        return get_quote_crypto(symbol, exchange)
    return get_quote_yfinance(symbol)

@router.post("/deposit")
def deposit(req: DepositReq):
    """Пополнить виртуальный баланс (приём средств от Дике)."""
    if req.amount <= 0:
        raise HTTPException(400, "Сумма должна быть > 0")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO deposits (amount, source) VALUES (?,?)", (req.amount, req.source))
    conn.execute("UPDATE balance SET virtual = virtual + ? WHERE id = 1", (req.amount,))
    conn.commit()
    balance = conn.execute("SELECT virtual FROM balance WHERE id=1").fetchone()[0]
    conn.close()
    logger.info(f"Депозит +{req.amount} от {req.source}. Баланс: {balance:.2f}")
    return {"status": "deposited", "amount": req.amount, "new_balance": round(balance, 2)}

@router.post("/trade")
def trade(req: TradeReq):
    """Paper trade — виртуальная сделка."""
    conn = sqlite3.connect(DB_PATH)
    balance = conn.execute("SELECT virtual FROM balance WHERE id=1").fetchone()[0]

    if req.action == "buy":
        quote_data = get_quote_crypto(req.symbol, req.exchange) if req.is_crypto else get_quote_yfinance(req.symbol)
        price = quote_data["price"]
        cost  = price * req.quantity
        if cost > balance:
            conn.close()
            raise HTTPException(400, f"Недостаточно средств: нужно {cost:.2f}, доступно {balance:.2f}")
        conn.execute("""
            INSERT INTO portfolio (symbol, quantity, buy_price)
            VALUES (?,?,?)
        """, (req.symbol.upper(), req.quantity, price))
        conn.execute("UPDATE balance SET virtual = virtual - ? WHERE id=1", (cost,))
        conn.commit()
        new_balance = conn.execute("SELECT virtual FROM balance WHERE id=1").fetchone()[0]
        conn.close()
        return {"action": "buy", "symbol": req.symbol, "quantity": req.quantity,
                "price": price, "cost": cost, "new_balance": round(new_balance, 2)}

    elif req.action == "sell":
        position = conn.execute("""
            SELECT id, buy_price, quantity FROM portfolio
            WHERE symbol=? AND is_open=1 ORDER BY bought_at ASC LIMIT 1
        """, (req.symbol.upper(),)).fetchone()
        if not position:
            conn.close()
            raise HTTPException(404, f"Нет открытой позиции по {req.symbol}")
        quote_data = get_quote_crypto(req.symbol, req.exchange) if req.is_crypto else get_quote_yfinance(req.symbol)
        price  = quote_data["price"]
        profit = (price - position[1]) * req.quantity
        conn.execute("UPDATE portfolio SET is_open=0, sold_at=datetime('now'), sell_price=? WHERE id=?",
                     (price, position[0]))
        conn.execute("UPDATE balance SET virtual = virtual + ? WHERE id=1", (price * req.quantity,))
        conn.commit()
        new_balance = conn.execute("SELECT virtual FROM balance WHERE id=1").fetchone()[0]
        conn.close()
        return {"action": "sell", "symbol": req.symbol, "price": price,
                "profit": round(profit, 2), "new_balance": round(new_balance, 2)}

    conn.close()
    raise HTTPException(400, "action должен быть 'buy' или 'sell'")

@router.post("/recommend")
async def recommend(req: RecommendReq):
    """LLM-рекомендации по портфелю."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    positions = conn.execute("SELECT * FROM portfolio WHERE is_open=1").fetchall()
    balance   = conn.execute("SELECT virtual FROM balance WHERE id=1").fetchone()[0]
    conn.close()
    summary = "\n".join(
        f"• {p['symbol']}: {p['quantity']} @ {p['buy_price']:.2f}" for p in positions
    ) or "Портфель пуст"
    advice = await llm_recommend(summary, balance, req.query or "")
    return {"advice": advice, "portfolio_size": len(positions), "free_balance": round(balance, 2),
            "disclaimer": "Не является финансовым советом. Виртуальный режим."}

@router.get("/portfolio")
def portfolio():
    """Текущий виртуальный портфель."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    positions = conn.execute("SELECT * FROM portfolio WHERE is_open=1 ORDER BY bought_at DESC").fetchall()
    balance   = conn.execute("SELECT virtual FROM balance WHERE id=1").fetchone()[0]
    conn.close()
    return {"positions": [dict(p) for p in positions], "free_balance": round(balance, 2)}

@router.get("/health")
def health():
    return {"agent": "Плутос", "alive": True}

app = FastAPI(title="Плутос — Инвестор")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8009)

# ── Сиен 3.0.0: подключаем расширения ──
try:
    from agents.plutos_ext import setup_plutos_ext
    setup_plutos_ext(app)
except Exception as _ext_err:
    import logging as _l
    _l.getLogger('plutos').warning(f'Расширения недоступны: {_ext_err}')
