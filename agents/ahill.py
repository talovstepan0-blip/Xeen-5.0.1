"""
agents/ahill.py — Агент Ахилл (Защитник VPS/прокси). Сиен 3.0.0.

Изменения:
  • Убрано дублирование app = FastAPI().
  • PySocks теперь опциональный импорт (не ломает старт без библиотеки).
  • Единый app с on_event startup.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp
from fastapi import APIRouter, FastAPI

logger = logging.getLogger("ahill")
logging.basicConfig(level=logging.INFO, format="[АХИЛЛ] %(message)s")

# PySocks опционален
try:
    import socks  # noqa: F401
    SOCKS_OK = True
except ImportError:
    SOCKS_OK = False

router = APIRouter(prefix="/proxy", tags=["ahill"])

# ══════════════════════════════════════════════════════════════════
# Конфигурация
# ══════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "socks5_host": "127.0.0.1",
    "socks5_port": 1080,
    "ping_interval_sec": 60,
    "ping_timeout_ms": 300,
    "vps_list": [
        {"name": "primary",  "host": "10.0.0.1", "port": 22},
        {"name": "backup-1", "host": "10.0.0.2", "port": 22},
        {"name": "backup-2", "host": "10.0.0.3", "port": 22},
    ],
    "kronos_url": "http://localhost:8001",
}


class AhillState:
    def __init__(self):
        self.config = DEFAULT_CONFIG.copy()
        self.current_vps_index: int = 0
        self.current_latency_ms: Optional[float] = None
        self.current_ip: Optional[str] = "unknown"
        self.status: str = "initializing"


state = AhillState()


# ══════════════════════════════════════════════════════════════════
# Утилиты
# ══════════════════════════════════════════════════════════════════

async def fetch_config_from_kronos() -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{state.config['kronos_url']}/secrets/get",
                params={"key": "vps_config"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info("Конфиг получен от Кроноса")
                    return data.get("value", {})
    except Exception as e:
        logger.warning(f"Кронос недоступен, дефолт: {e}")
    return {}


async def tcp_ping(host: str, port: int, timeout: float = 1.0) -> Optional[float]:
    try:
        start = time.monotonic()
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        elapsed = (time.monotonic() - start) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return round(elapsed, 2)
    except Exception:
        return None


async def switch_to_next_vps() -> None:
    vps_list = state.config["vps_list"]
    if not vps_list:
        return
    old = state.current_vps_index
    state.current_vps_index = (state.current_vps_index + 1) % len(vps_list)
    new_vps = vps_list[state.current_vps_index]
    logger.warning(
        f"Смена VPS: {vps_list[old]['name']} → {new_vps['name']} ({new_vps['host']})"
    )


# ══════════════════════════════════════════════════════════════════
# Monitor loop
# ══════════════════════════════════════════════════════════════════

async def monitor_loop():
    kronos_config = await fetch_config_from_kronos()
    if kronos_config:
        state.config.update(kronos_config)

    while True:
        try:
            vps = state.config["vps_list"][state.current_vps_index]
            latency = await tcp_ping(vps["host"], vps["port"])

            if latency is None:
                logger.error(f"VPS {vps['name']} недоступен! Переключение...")
                state.status = "failed"
                await switch_to_next_vps()
            elif latency > state.config["ping_timeout_ms"]:
                logger.warning(
                    f"Высокая задержка: {latency}ms > "
                    f"{state.config['ping_timeout_ms']}ms"
                )
                state.status = "degraded"
                await switch_to_next_vps()
            else:
                state.status = "ok"

            state.current_latency_ms = latency
        except Exception as e:
            logger.error(f"monitor_loop: {e}")
            state.status = "error"

        await asyncio.sleep(state.config["ping_interval_sec"])


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

@router.get("/status")
async def proxy_status():
    vps = state.config["vps_list"][state.current_vps_index]
    return {
        "agent": "Ахилл",
        "status": state.status,
        "current_vps": vps["name"],
        "vps_host": vps["host"],
        "latency_ms": state.current_latency_ms,
        "proxy": f"socks5://{state.config['socks5_host']}:{state.config['socks5_port']}",
        "external_ip": state.current_ip,
        "pysocks": SOCKS_OK,
    }


@router.post("/switch")
async def force_switch():
    logger.info("Команда: смена сервера")
    await switch_to_next_vps()
    vps = state.config["vps_list"][state.current_vps_index]
    return {
        "message": f"Переключено на {vps['name']} ({vps['host']})",
        "status": "switched",
    }


@router.get("/health")
async def health():
    return {"agent": "Ахилл", "alive": True, "status": state.status}


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Ахилл — Защитник")
app.include_router(router)


@app.on_event("startup")
async def _start():
    asyncio.create_task(monitor_loop())
    logger.info("Агент Ахилл запущен")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
