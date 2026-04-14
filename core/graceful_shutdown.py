"""
core/graceful_shutdown.py — Менеджер корректной остановки сервисов.

Регистрирует обработчики SIGTERM/SIGINT, вызывает зарегистрированные
async-колбэки, даёт им завершить работу (сохранить БД, закрыть сокеты,
сбросить задачи в файл).

Использование (в агенте или оркестраторе):

    from core.graceful_shutdown import shutdown_manager

    async def on_shutdown():
        await db.close()

    shutdown_manager.register(on_shutdown)
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Awaitable, Callable

logger = logging.getLogger("graceful_shutdown")

Handler = Callable[[], Awaitable[None]]


class ShutdownManager:
    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._triggered = False
        self._installed = False

    def register(self, handler: Handler) -> None:
        """Зарегистрировать async-функцию, вызываемую при остановке."""
        self._handlers.append(handler)
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        if self._installed:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.trigger(s)))
            except (NotImplementedError, RuntimeError):
                # Windows: add_signal_handler не работает, fallback на signal.signal
                try:
                    signal.signal(sig, lambda *_: asyncio.create_task(self.trigger(sig)))
                except Exception:
                    pass
        self._installed = True

    async def trigger(self, sig: signal.Signals | None = None) -> None:
        if self._triggered:
            return
        self._triggered = True
        if sig is not None:
            logger.info(f"Получен сигнал {sig.name}, начинается остановка...")
        for handler in list(self._handlers):
            try:
                await asyncio.wait_for(handler(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(f"Handler {handler.__name__} превысил таймаут 10с")
            except Exception as e:
                logger.error(f"Handler {handler.__name__}: {e}")
        logger.info("Graceful shutdown завершён.")


# Глобальный singleton
shutdown_manager = ShutdownManager()
