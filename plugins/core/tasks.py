"""
plugins/core/tasks.py — Локальный плагин задач (приоритеты, подзадачи, статусы).

Не путать с агентом Вэнь (он использует SQLite + Telegram).
Этот плагин — отдельное локальное хранилище в JSON для использования
из локальных команд HUD без обращения к серверу.

API:
    plugin = TasksPlugin()
    tid = plugin.create("Закончить отчёт", priority="high", deadline="2026-04-15")
    plugin.add_subtask(tid, "Собрать данные")
    plugin.complete(tid)
    plugin.delete(tid)
    summary = plugin.summary()
    kanban = plugin.kanban()
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin

PRIORITIES = ("high", "medium", "low")
STATUSES = ("todo", "progress", "done")


class TasksPlugin(CorePlugin):
    name = "tasks"

    def __init__(self) -> None:
        super().__init__()
        self._tasks: dict = self.get_json("tasks.json", default={})

    def _save(self) -> None:
        self.save_json("tasks.json", self._tasks)

    def create(self, title: str, priority: str = "medium",
               deadline: Optional[str] = None,
               description: str = "") -> str:
        if priority not in PRIORITIES:
            priority = "medium"
        tid = str(uuid4())[:8]
        self._tasks[tid] = {
            "id": tid,
            "title": title,
            "description": description,
            "priority": priority,
            "status": "todo",
            "deadline": deadline,
            "subtasks": [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self._save()
        return tid

    def update(self, tid: str, **fields) -> bool:
        if tid not in self._tasks:
            return False
        self._tasks[tid].update(fields)
        self._tasks[tid]["updated_at"] = datetime.now().isoformat()
        self._save()
        return True

    def add_subtask(self, tid: str, text: str) -> bool:
        if tid not in self._tasks:
            return False
        self._tasks[tid]["subtasks"].append({"text": text, "done": False})
        self._save()
        return True

    def complete_subtask(self, tid: str, idx: int) -> bool:
        if tid not in self._tasks:
            return False
        subs = self._tasks[tid]["subtasks"]
        if 0 <= idx < len(subs):
            subs[idx]["done"] = True
            self._save()
            return True
        return False

    def set_status(self, tid: str, status: str) -> bool:
        if status not in STATUSES:
            return False
        return self.update(tid, status=status)

    def complete(self, tid: str) -> bool:
        return self.set_status(tid, "done")

    def delete(self, tid: str) -> bool:
        if tid in self._tasks:
            del self._tasks[tid]
            self._save()
            return True
        return False

    def get(self, tid: str) -> Optional[dict]:
        return self._tasks.get(tid)

    def list_all(self, status: Optional[str] = None,
                 priority: Optional[str] = None) -> list[dict]:
        items = list(self._tasks.values())
        if status:
            items = [t for t in items if t.get("status") == status]
        if priority:
            items = [t for t in items if t.get("priority") == priority]
        return sorted(items, key=lambda t: (
            {"high": 0, "medium": 1, "low": 2}.get(t.get("priority", "medium"), 1),
            t.get("deadline") or "9999",
        ))

    def summary(self) -> dict:
        all_tasks = list(self._tasks.values())
        return {
            "total": len(all_tasks),
            "by_status": {s: sum(1 for t in all_tasks if t.get("status") == s)
                          for s in STATUSES},
            "by_priority": {p: sum(1 for t in all_tasks if t.get("priority") == p)
                            for p in PRIORITIES},
            "with_deadline": sum(1 for t in all_tasks if t.get("deadline")),
        }

    def kanban(self) -> dict:
        return {s: self.list_all(status=s) for s in STATUSES}

    def format_summary(self) -> str:
        s = self.summary()
        return (
            f"📋 **Всего задач:** {s['total']}\n"
            f"• Сделать: {s['by_status']['todo']}\n"
            f"• В работе: {s['by_status']['progress']}\n"
            f"• Готово: {s['by_status']['done']}\n"
            f"• С дедлайном: {s['with_deadline']}"
        )
