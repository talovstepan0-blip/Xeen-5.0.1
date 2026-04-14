"""
plugins/productivity/goals.py — Долгосрочные цели с подзадачами.

Прогресс цели = % выполненных подзадач.
Цель автоматически становится "completed", когда все подзадачи готовы.

API:
    g = GoalsPlugin()
    gid = g.create("Выучить английский B2", deadline="2026-12-31")
    g.add_subtask(gid, "Закончить курс A2")
    g.add_subtask(gid, "Сдать экзамен B1")
    g.complete_subtask(gid, 0)
    progress = g.get_progress(gid)   # 50.0
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin


class GoalsPlugin(CorePlugin):
    name = "goals"

    def __init__(self) -> None:
        super().__init__()
        self._goals: dict = self.get_json("goals.json", default={})

    def _save(self) -> None:
        self.save_json("goals.json", self._goals)

    def _recalc(self, gid: str) -> None:
        goal = self._goals.get(gid)
        if not goal:
            return
        subs = goal.get("subtasks", [])
        if not subs:
            goal["progress"] = 0.0
            goal["status"] = "active"
            return
        done = sum(1 for s in subs if s.get("done"))
        goal["progress"] = round(done / len(subs) * 100, 1)
        goal["status"] = "completed" if done == len(subs) else "active"

    def create(self, title: str, description: str = "",
               deadline: Optional[str] = None) -> str:
        gid = str(uuid4())[:8]
        self._goals[gid] = {
            "id": gid,
            "title": title,
            "description": description,
            "deadline": deadline,
            "subtasks": [],
            "progress": 0.0,
            "status": "active",
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        return gid

    def add_subtask(self, gid: str, text: str) -> bool:
        if gid not in self._goals:
            return False
        self._goals[gid]["subtasks"].append({"text": text, "done": False})
        self._recalc(gid)
        self._save()
        return True

    def complete_subtask(self, gid: str, idx: int) -> bool:
        if gid not in self._goals:
            return False
        subs = self._goals[gid]["subtasks"]
        if 0 <= idx < len(subs):
            subs[idx]["done"] = True
            self._recalc(gid)
            self._save()
            return True
        return False

    def uncomplete_subtask(self, gid: str, idx: int) -> bool:
        if gid not in self._goals:
            return False
        subs = self._goals[gid]["subtasks"]
        if 0 <= idx < len(subs):
            subs[idx]["done"] = False
            self._recalc(gid)
            self._save()
            return True
        return False

    def delete(self, gid: str) -> bool:
        if gid in self._goals:
            del self._goals[gid]
            self._save()
            return True
        return False

    def get(self, gid: str) -> Optional[dict]:
        return self._goals.get(gid)

    def get_progress(self, gid: str) -> float:
        g = self._goals.get(gid)
        return g.get("progress", 0.0) if g else 0.0

    def list_all(self, status: Optional[str] = None) -> list[dict]:
        items = list(self._goals.values())
        if status:
            items = [g for g in items if g.get("status") == status]
        return sorted(items, key=lambda g: g.get("deadline") or "9999")

    def format_goals(self) -> str:
        active = self.list_all(status="active")
        if not active:
            return "🎯 Активных целей нет."
        lines = ["🎯 **Цели:**\n"]
        for g in active:
            bar = "█" * int(g["progress"] / 10) + "░" * (10 - int(g["progress"] / 10))
            deadline = f" (до {g['deadline']})" if g.get("deadline") else ""
            lines.append(f"• **{g['title']}**{deadline}")
            lines.append(f"  `{bar}` {g['progress']}%")
        return "\n".join(lines)
