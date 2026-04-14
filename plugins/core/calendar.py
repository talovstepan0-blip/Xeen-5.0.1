"""
plugins/core/calendar.py — Локальный календарь.

Поддерживает:
  • Точные даты: "2026-04-15 18:00", "15.04.2026 18:00"
  • Относительные: "сегодня 15:00", "завтра 10:00", "послезавтра 9:00"
  • Дни недели: "в пятницу 12:00", "в понедельник 9:00"

API:
    plugin = CalendarPlugin()
    eid = plugin.add_event("Встреча", "завтра 10:00", description="Zoom")
    upcoming = plugin.upcoming(7)
    plugin.complete(eid)
    plugin.delete(eid)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin


WEEKDAYS = {
    "понедельник": 0, "понедельника": 0, "понедельнику": 0, "пн": 0,
    "вторник": 1, "вторника": 1, "вторнику": 1, "вт": 1,
    "среда": 2, "среду": 2, "среды": 2, "среде": 2, "ср": 2,
    "четверг": 3, "четверга": 3, "четвергу": 3, "чт": 3,
    "пятница": 4, "пятницу": 4, "пятницы": 4, "пятнице": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "субботы": 5, "субботе": 5, "сб": 5,
    "воскресенье": 6, "воскресенья": 6, "воскресенью": 6, "вс": 6,
}


def parse_when(when: str) -> Optional[datetime]:
    """Парсит дату/время из естественной строки. Возвращает datetime или None."""
    if not when:
        return None
    s = when.lower().strip()
    now = datetime.now()

    # Время в строке: только формат HH:MM (не точка, чтобы не путать с датой)
    hh, mm = 9, 0
    time_match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", s)
    if time_match:
        hh = int(time_match.group(1))
        mm = int(time_match.group(2))

    # ISO формат
    try:
        if "T" in when:
            return datetime.fromisoformat(when)
    except ValueError:
        pass

    # YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return datetime(y, mo, d, hh, mm)

    # DD.MM.YYYY или DD.MM
    m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", s)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now.year
        if y < 100:
            y += 2000
        try:
            return datetime(y, mo, d, hh, mm)
        except ValueError:
            return None

    # "сегодня"
    if "сегодня" in s:
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    # "завтра"
    if "завтра" in s:
        if "после" in s or "послезавтра" in s:
            return (now + timedelta(days=2)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return (now + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)

    # "послезавтра"
    if "послезавтра" in s:
        return (now + timedelta(days=2)).replace(hour=hh, minute=mm, second=0, microsecond=0)

    # Дни недели
    for name, weekday in WEEKDAYS.items():
        if name in s:
            days_ahead = (weekday - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (now + timedelta(days=days_ahead)).replace(
                hour=hh, minute=mm, second=0, microsecond=0
            )

    return None


class CalendarPlugin(CorePlugin):
    name = "calendar"

    def __init__(self) -> None:
        super().__init__()
        self._events: dict = self.get_json("events.json", default={})

    def _save(self) -> None:
        self.save_json("events.json", self._events)

    def add_event(self, title: str, when: str,
                  description: str = "") -> Optional[str]:
        dt = parse_when(when)
        if dt is None:
            return None
        eid = str(uuid4())[:8]
        self._events[eid] = {
            "id": eid,
            "title": title,
            "description": description,
            "datetime": dt.isoformat(),
            "completed": False,
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        return eid

    def upcoming(self, days: int = 7) -> list[dict]:
        now = datetime.now()
        until = now + timedelta(days=days)
        result = []
        for ev in self._events.values():
            if ev.get("completed"):
                continue
            try:
                dt = datetime.fromisoformat(ev["datetime"])
            except (ValueError, KeyError):
                continue
            if now <= dt <= until:
                result.append(ev)
        return sorted(result, key=lambda e: e["datetime"])

    def all_events(self) -> list[dict]:
        return sorted(self._events.values(),
                      key=lambda e: e.get("datetime", ""))

    def complete(self, eid: str) -> bool:
        if eid not in self._events:
            return False
        self._events[eid]["completed"] = True
        self._save()
        return True

    def delete(self, eid: str) -> bool:
        if eid in self._events:
            del self._events[eid]
            self._save()
            return True
        return False

    def get(self, eid: str) -> Optional[dict]:
        return self._events.get(eid)

    def format_upcoming(self, days: int = 7) -> str:
        events = self.upcoming(days)
        if not events:
            return f"📅 Ближайшие {days} дней — событий нет."
        lines = [f"📅 **События на {days} дней:**\n"]
        for ev in events:
            try:
                dt = datetime.fromisoformat(ev["datetime"])
                when_str = dt.strftime("%d.%m %H:%M")
            except (ValueError, KeyError):
                when_str = ev.get("datetime", "?")
            lines.append(f"• `{when_str}` — **{ev['title']}**")
            if ev.get("description"):
                lines.append(f"  {ev['description'][:80]}")
        return "\n".join(lines)
