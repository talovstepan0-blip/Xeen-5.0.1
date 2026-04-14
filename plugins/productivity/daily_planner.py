"""
plugins/productivity/daily_planner.py — План на день по часам.

Слоты с 8:00 до 22:00 (15 слотов по часу).
План привязан к дате (хранится отдельный план на каждый день).

API:
    p = DailyPlanner()
    p.add_task("2026-04-15", "10:00", "Митинг с командой")
    p.add_task("2026-04-15", "14:00", "Закрыть тикет SIEN-42")
    plan = p.get_plan("2026-04-15")
    plan = p.get_plan()  # сегодняшний
"""

from __future__ import annotations

from datetime import date as date_type, datetime
from typing import Optional

from plugins import CorePlugin

SLOTS = [f"{h:02d}:00" for h in range(8, 23)]   # 08:00..22:00


class DailyPlanner(CorePlugin):
    name = "daily_planner"

    def __init__(self) -> None:
        super().__init__()
        self._plans: dict = self.get_json("plans.json", default={})

    def _save(self) -> None:
        self.save_json("plans.json", self._plans)

    def _normalize_date(self, d: Optional[str]) -> str:
        if not d:
            return date_type.today().isoformat()
        try:
            return date_type.fromisoformat(d).isoformat()
        except ValueError:
            return date_type.today().isoformat()

    def _ensure_plan(self, date_str: str) -> dict:
        if date_str not in self._plans:
            self._plans[date_str] = {slot: [] for slot in SLOTS}
        return self._plans[date_str]

    def add_task(self, date: Optional[str], slot: str, text: str) -> bool:
        if slot not in SLOTS:
            return False
        date_str = self._normalize_date(date)
        plan = self._ensure_plan(date_str)
        plan[slot].append({
            "text": text,
            "done": False,
            "added_at": datetime.now().isoformat(),
        })
        self._save()
        return True

    def remove_task(self, date: Optional[str], slot: str, idx: int) -> bool:
        date_str = self._normalize_date(date)
        if date_str not in self._plans:
            return False
        items = self._plans[date_str].get(slot, [])
        if 0 <= idx < len(items):
            items.pop(idx)
            self._save()
            return True
        return False

    def mark_done(self, date: Optional[str], slot: str, idx: int) -> bool:
        date_str = self._normalize_date(date)
        if date_str not in self._plans:
            return False
        items = self._plans[date_str].get(slot, [])
        if 0 <= idx < len(items):
            items[idx]["done"] = True
            self._save()
            return True
        return False

    def get_plan(self, date: Optional[str] = None) -> dict:
        date_str = self._normalize_date(date)
        plan = self._plans.get(date_str, {slot: [] for slot in SLOTS})
        return {"date": date_str, "slots": plan}

    def list_planned_dates(self) -> list[str]:
        return sorted(self._plans.keys())

    def clear_day(self, date: Optional[str]) -> bool:
        date_str = self._normalize_date(date)
        if date_str in self._plans:
            del self._plans[date_str]
            self._save()
            return True
        return False

    def format_plan(self, date: Optional[str] = None) -> str:
        p = self.get_plan(date)
        lines = [f"📅 **План на {p['date']}:**\n"]
        any_tasks = False
        for slot in SLOTS:
            tasks = p["slots"].get(slot, [])
            if tasks:
                any_tasks = True
                lines.append(f"\n**{slot}**")
                for t in tasks:
                    check = "✓" if t.get("done") else "○"
                    lines.append(f"  {check} {t['text']}")
        if not any_tasks:
            lines.append("\n_План пуст_")
        return "\n".join(lines)
