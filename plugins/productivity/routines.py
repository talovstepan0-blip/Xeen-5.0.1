"""
plugins/productivity/routines.py — Ежедневные и еженедельные рутины.

Рутина — список дел, которые повторяются по расписанию (daily/weekly).
Хранит дату последнего выполнения и историю.

API:
    r = RoutinesPlugin()
    rid = r.create("Утренняя рутина", "daily", items=[
        "Зарядка", "Душ", "Завтрак", "Планы на день"
    ])
    r.mark_item_done(rid, 0)
    r.mark_completed(rid)         # вся рутина выполнена
    today = r.due_today()
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin

PERIODS = ("daily", "weekly")


class RoutinesPlugin(CorePlugin):
    name = "routines"

    def __init__(self) -> None:
        super().__init__()
        self._routines: dict = self.get_json("routines.json", default={})

    def _save(self) -> None:
        self.save_json("routines.json", self._routines)

    def create(self, name: str, period: str = "daily",
               items: Optional[list[str]] = None) -> str:
        if period not in PERIODS:
            period = "daily"
        rid = str(uuid4())[:8]
        self._routines[rid] = {
            "id": rid,
            "name": name,
            "period": period,
            "items": [{"text": t, "done": False} for t in (items or [])],
            "last_completed_at": None,
            "history": [],
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        return rid

    def add_item(self, rid: str, text: str) -> bool:
        if rid not in self._routines:
            return False
        self._routines[rid]["items"].append({"text": text, "done": False})
        self._save()
        return True

    def mark_item_done(self, rid: str, idx: int) -> bool:
        if rid not in self._routines:
            return False
        items = self._routines[rid]["items"]
        if 0 <= idx < len(items):
            items[idx]["done"] = True
            self._save()
            return True
        return False

    def mark_completed(self, rid: str) -> bool:
        """Отмечает всю рутину как выполненную, сбрасывает чек-листы."""
        if rid not in self._routines:
            return False
        r = self._routines[rid]
        now = datetime.now().isoformat()
        r["last_completed_at"] = now
        r["history"] = (r.get("history", []) + [now])[-30:]  # хранить последние 30
        for item in r["items"]:
            item["done"] = False
        self._save()
        return True

    def delete(self, rid: str) -> bool:
        if rid in self._routines:
            del self._routines[rid]
            self._save()
            return True
        return False

    def list_all(self) -> list[dict]:
        return list(self._routines.values())

    def due_today(self) -> list[dict]:
        """Возвращает рутины, которые сегодня нужно выполнить."""
        now = datetime.now()
        result = []
        for r in self._routines.values():
            last = r.get("last_completed_at")
            if last is None:
                result.append(r)
                continue
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                result.append(r)
                continue
            if r["period"] == "daily" and last_dt.date() < now.date():
                result.append(r)
            elif r["period"] == "weekly" and (now - last_dt) >= timedelta(days=7):
                result.append(r)
        return result

    def format_due(self) -> str:
        due = self.due_today()
        if not due:
            return "✅ Все рутины на сегодня выполнены!"
        lines = ["📋 **Рутины на сегодня:**\n"]
        for r in due:
            done = sum(1 for it in r["items"] if it.get("done"))
            total = len(r["items"])
            lines.append(f"• **{r['name']}** ({done}/{total})")
            for i, item in enumerate(r["items"][:8]):
                check = "✓" if item.get("done") else "○"
                lines.append(f"  {check} {item['text']}")
        return "\n".join(lines)
