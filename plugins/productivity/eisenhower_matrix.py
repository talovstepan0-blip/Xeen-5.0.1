"""
plugins/productivity/eisenhower_matrix.py — Матрица Эйзенхауэра.

Квадранты:
  do        — Срочно + Важно         → "Сделать сейчас"
  schedule  — Не срочно + Важно      → "Запланировать"
  delegate  — Срочно + Не важно      → "Делегировать"
  delete    — Не срочно + Не важно   → "Удалить"

API:
    m = EisenhowerMatrix()
    tid = m.add("Подготовить презентацию", important=True, urgent=True)
    m.complete(tid)
    matrix = m.get_matrix()
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin

QUADRANTS = ("do", "schedule", "delegate", "delete")
QUADRANT_LABELS = {
    "do": "Сделать сейчас",
    "schedule": "Запланировать",
    "delegate": "Делегировать",
    "delete": "Удалить",
}


def classify(important: bool, urgent: bool) -> str:
    if important and urgent:
        return "do"
    if important and not urgent:
        return "schedule"
    if not important and urgent:
        return "delegate"
    return "delete"


class EisenhowerMatrix(CorePlugin):
    name = "eisenhower"

    def __init__(self) -> None:
        super().__init__()
        self._tasks: dict = self.get_json("tasks.json", default={})

    def _save(self) -> None:
        self.save_json("tasks.json", self._tasks)

    def add(self, title: str, important: bool, urgent: bool,
            description: str = "") -> str:
        tid = str(uuid4())[:8]
        self._tasks[tid] = {
            "id": tid,
            "title": title,
            "description": description,
            "important": important,
            "urgent": urgent,
            "quadrant": classify(important, urgent),
            "completed": False,
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        return tid

    def reclassify(self, tid: str, important: bool, urgent: bool) -> bool:
        if tid not in self._tasks:
            return False
        self._tasks[tid]["important"] = important
        self._tasks[tid]["urgent"] = urgent
        self._tasks[tid]["quadrant"] = classify(important, urgent)
        self._save()
        return True

    def complete(self, tid: str) -> bool:
        if tid not in self._tasks:
            return False
        self._tasks[tid]["completed"] = True
        self._tasks[tid]["completed_at"] = datetime.now().isoformat()
        self._save()
        return True

    def delete(self, tid: str) -> bool:
        if tid in self._tasks:
            del self._tasks[tid]
            self._save()
            return True
        return False

    def get_matrix(self, include_completed: bool = False) -> dict:
        result: dict[str, list[dict]] = {q: [] for q in QUADRANTS}
        for t in self._tasks.values():
            if not include_completed and t.get("completed"):
                continue
            result[t["quadrant"]].append(t)
        return result

    def format_matrix(self) -> str:
        m = self.get_matrix()
        lines = ["🎯 **Матрица Эйзенхауэра:**"]
        for q in QUADRANTS:
            label = QUADRANT_LABELS[q]
            tasks = m[q]
            lines.append(f"\n**{label}** ({len(tasks)})")
            for t in tasks[:5]:
                lines.append(f"  • {t['title']}")
        return "\n".join(lines)
