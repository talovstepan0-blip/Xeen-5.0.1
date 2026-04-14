"""
plugins/productivity/habit_checklist.py — Чек-лист привычек.

Каждая привычка имеет:
  • Предпочтительное время выполнения (например, "08:00")
  • Историю выполнений (timestamps)
  • Streak (дней подряд)
  • Автосброс ежедневный — флаг "сделано сегодня" сбрасывается при смене даты

API:
    h = HabitChecklist()
    hid = h.create("Зарядка", preferred_time="08:00")
    h.mark_done(hid)
    streak = h.get_streak(hid)
    today = h.due_today()
"""

from __future__ import annotations

from datetime import date as date_type, datetime, timedelta
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin


class HabitChecklist(CorePlugin):
    name = "habits"

    def __init__(self) -> None:
        super().__init__()
        self._habits: dict = self.get_json("habits.json", default={})
        self._daily_reset()

    def _save(self) -> None:
        self.save_json("habits.json", self._habits)

    def _daily_reset(self) -> None:
        """Сбрасывает 'done_today' если прошёл день."""
        today = date_type.today().isoformat()
        changed = False
        for h in self._habits.values():
            if h.get("done_today_date") != today:
                h["done_today"] = False
                h["done_today_date"] = today
                changed = True
        if changed:
            self._save()

    def create(self, name: str, preferred_time: Optional[str] = None,
               description: str = "") -> str:
        hid = str(uuid4())[:8]
        self._habits[hid] = {
            "id": hid,
            "name": name,
            "description": description,
            "preferred_time": preferred_time,
            "history": [],
            "done_today": False,
            "done_today_date": date_type.today().isoformat(),
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        return hid

    def mark_done(self, hid: str) -> bool:
        self._daily_reset()
        if hid not in self._habits:
            return False
        h = self._habits[hid]
        if h.get("done_today"):
            return True
        h["done_today"] = True
        h["done_today_date"] = date_type.today().isoformat()
        h["history"] = (h.get("history", []) + [datetime.now().isoformat()])[-365:]
        self._save()
        return True

    def unmark(self, hid: str) -> bool:
        if hid not in self._habits:
            return False
        h = self._habits[hid]
        h["done_today"] = False
        # Удаляем последнюю отметку, если она сегодня
        history = h.get("history", [])
        today = date_type.today().isoformat()
        if history and history[-1].startswith(today):
            history.pop()
        self._save()
        return True

    def delete(self, hid: str) -> bool:
        if hid in self._habits:
            del self._habits[hid]
            self._save()
            return True
        return False

    def get_streak(self, hid: str) -> int:
        h = self._habits.get(hid)
        if not h:
            return 0
        history = h.get("history", [])
        if not history:
            return 0
        try:
            dates = sorted({
                datetime.fromisoformat(ts).date() for ts in history
            }, reverse=True)
        except ValueError:
            return 0
        if not dates:
            return 0
        today = date_type.today()
        if dates[0] < today - timedelta(days=1):
            return 0
        streak = 1
        for i in range(1, len(dates)):
            if (dates[i - 1] - dates[i]).days == 1:
                streak += 1
            else:
                break
        return streak

    def list_all(self) -> list[dict]:
        self._daily_reset()
        result = []
        for h in self._habits.values():
            result.append({**h, "streak": self.get_streak(h["id"])})
        return sorted(result, key=lambda h: h.get("preferred_time") or "99:99")

    def due_today(self) -> list[dict]:
        return [h for h in self.list_all() if not h.get("done_today")]

    def format_today(self) -> str:
        all_habits = self.list_all()
        if not all_habits:
            return "🎯 Привычек пока нет."
        lines = ["🎯 **Привычки на сегодня:**\n"]
        for h in all_habits:
            check = "✓" if h.get("done_today") else "○"
            time = f" ({h['preferred_time']})" if h.get("preferred_time") else ""
            streak = f" 🔥 {h['streak']}" if h.get("streak", 0) > 0 else ""
            lines.append(f"{check} **{h['name']}**{time}{streak}")
        return "\n".join(lines)
