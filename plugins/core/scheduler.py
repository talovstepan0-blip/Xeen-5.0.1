"""
plugins/core/scheduler.py — Фоновый планировщик напоминаний.

Поддерживает:
  • Одноразовые напоминания (через N минут или в конкретное время)
  • Повторяющиеся: daily, weekly, weekdays
  • Колбэки выполняются в отдельном потоке

API:
    plugin = SchedulerPlugin()
    plugin.start(callback=lambda r: print(r))
    plugin.add("Принять таблетку", in_minutes=30)
    plugin.add("Митинг", at="2026-04-15 10:00")
    plugin.add("Зарядка", repeat="weekdays", at="08:00")
    plugin.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional
from uuid import uuid4

from plugins import CorePlugin
from plugins.core.calendar import parse_when

logger = logging.getLogger("plugins.scheduler")

CHECK_INTERVAL_SEC = 30


class SchedulerPlugin(CorePlugin):
    name = "scheduler"

    def __init__(self) -> None:
        super().__init__()
        self._reminders: dict = self.get_json("reminders.json", default={})
        self._callback: Optional[Callable[[dict], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _save(self) -> None:
        self.save_json("reminders.json", self._reminders)

    def add(self, text: str,
            in_minutes: Optional[int] = None,
            at: Optional[str] = None,
            repeat: Optional[str] = None) -> str:
        """
        Добавляет напоминание.
        :param in_minutes: через N минут
        :param at: на конкретное время — "сегодня 15:00", "2026-04-15 10:00", "08:00"
        :param repeat: daily | weekly | weekdays | None
        """
        rid = str(uuid4())[:8]
        if in_minutes is not None:
            next_at = (datetime.now() + timedelta(minutes=in_minutes)).isoformat()
        elif at:
            dt = parse_when(at)
            if dt is None:
                # "08:00" — сегодня в это время
                m = at.strip()
                try:
                    h, mi = map(int, m.split(":"))
                    now = datetime.now()
                    dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                    if dt < now:
                        dt += timedelta(days=1)
                except (ValueError, AttributeError):
                    return ""
            next_at = dt.isoformat()
        else:
            return ""

        self._reminders[rid] = {
            "id": rid,
            "text": text,
            "next_at": next_at,
            "repeat": repeat,
            "created_at": datetime.now().isoformat(),
            "fired_count": 0,
        }
        self._save()
        return rid

    def remove(self, rid: str) -> bool:
        if rid in self._reminders:
            del self._reminders[rid]
            self._save()
            return True
        return False

    def list_all(self) -> list[dict]:
        return sorted(self._reminders.values(),
                      key=lambda r: r.get("next_at", ""))

    def _next_after(self, current: datetime, repeat: str) -> Optional[datetime]:
        if repeat == "daily":
            return current + timedelta(days=1)
        if repeat == "weekly":
            return current + timedelta(days=7)
        if repeat == "weekdays":
            nxt = current + timedelta(days=1)
            while nxt.weekday() >= 5:  # пропускаем сб/вс
                nxt += timedelta(days=1)
            return nxt
        return None

    def _loop(self) -> None:
        logger.info("Scheduler thread запущен")
        while not self._stop_event.is_set():
            try:
                now = datetime.now()
                to_fire: list[tuple[str, dict]] = []
                for rid, rem in list(self._reminders.items()):
                    try:
                        next_at = datetime.fromisoformat(rem["next_at"])
                    except (ValueError, KeyError):
                        continue
                    if next_at <= now:
                        to_fire.append((rid, rem))

                for rid, rem in to_fire:
                    if self._callback:
                        try:
                            self._callback(rem)
                        except Exception as e:
                            logger.error(f"callback: {e}")

                    rem["fired_count"] = rem.get("fired_count", 0) + 1
                    nxt = self._next_after(
                        datetime.fromisoformat(rem["next_at"]),
                        rem.get("repeat") or "",
                    )
                    if nxt:
                        rem["next_at"] = nxt.isoformat()
                    else:
                        del self._reminders[rid]
                if to_fire:
                    self._save()
            except Exception as e:
                logger.error(f"scheduler loop: {e}")
            self._stop_event.wait(CHECK_INTERVAL_SEC)
        logger.info("Scheduler thread остановлен")

    def start(self, callback: Optional[Callable[[dict], None]] = None) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._callback = callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="SchedulerLoop")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
