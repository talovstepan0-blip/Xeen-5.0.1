"""
plugins/productivity/project_kanban.py — Канбан по проектам.

Каждый проект имеет свою независимую доску с тремя колонками.
Проекты можно архивировать без удаления.

API:
    pk = ProjectKanban()
    pid = pk.create_project("Сиен 3.0")
    cid = pk.add_card(pid, "Доделать плагины", "todo")
    pk.move_card(pid, cid, "progress")
    board = pk.get_project_board(pid)
    pk.archive_project(pid)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin

COLUMNS = ("todo", "progress", "done")
COLUMN_LABELS = {"todo": "К выполнению", "progress": "В работе", "done": "Готово"}


class ProjectKanban(CorePlugin):
    name = "project_kanban"

    def __init__(self) -> None:
        super().__init__()
        self._projects: dict = self.get_json("projects.json", default={})

    def _save(self) -> None:
        self.save_json("projects.json", self._projects)

    # ── Проекты ───────────────────────────────────────────────────

    def create_project(self, name: str, description: str = "") -> str:
        pid = str(uuid4())[:8]
        self._projects[pid] = {
            "id": pid,
            "name": name,
            "description": description,
            "archived": False,
            "created_at": datetime.now().isoformat(),
            "board": {c: [] for c in COLUMNS},
        }
        self._save()
        return pid

    def archive_project(self, pid: str) -> bool:
        if pid not in self._projects:
            return False
        self._projects[pid]["archived"] = True
        self._projects[pid]["archived_at"] = datetime.now().isoformat()
        self._save()
        return True

    def unarchive_project(self, pid: str) -> bool:
        if pid not in self._projects:
            return False
        self._projects[pid]["archived"] = False
        self._save()
        return True

    def delete_project(self, pid: str) -> bool:
        if pid in self._projects:
            del self._projects[pid]
            self._save()
            return True
        return False

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        result = []
        for p in self._projects.values():
            if not include_archived and p.get("archived"):
                continue
            stats = {c: len(p["board"].get(c, [])) for c in COLUMNS}
            result.append({**p, "stats": stats})
        return sorted(result, key=lambda p: p.get("created_at", ""))

    # ── Карточки ──────────────────────────────────────────────────

    def add_card(self, pid: str, title: str, column: str = "todo") -> Optional[str]:
        if pid not in self._projects:
            return None
        if column not in COLUMNS:
            column = "todo"
        cid = str(uuid4())[:8]
        self._projects[pid]["board"][column].append({
            "id": cid,
            "title": title,
            "created_at": datetime.now().isoformat(),
        })
        self._save()
        return cid

    def move_card(self, pid: str, cid: str, to_column: str) -> bool:
        if pid not in self._projects or to_column not in COLUMNS:
            return False
        board = self._projects[pid]["board"]
        for col in COLUMNS:
            for idx, c in enumerate(board[col]):
                if c["id"] == cid:
                    if col == to_column:
                        return True
                    card = board[col].pop(idx)
                    card["moved_at"] = datetime.now().isoformat()
                    board[to_column].append(card)
                    self._save()
                    return True
        return False

    def delete_card(self, pid: str, cid: str) -> bool:
        if pid not in self._projects:
            return False
        board = self._projects[pid]["board"]
        for col in COLUMNS:
            for idx, c in enumerate(board[col]):
                if c["id"] == cid:
                    board[col].pop(idx)
                    self._save()
                    return True
        return False

    def get_project_board(self, pid: str) -> Optional[dict]:
        if pid not in self._projects:
            return None
        p = self._projects[pid]
        return {
            "name": p["name"],
            "archived": p.get("archived", False),
            "columns": {COLUMN_LABELS[c]: list(p["board"].get(c, [])) for c in COLUMNS},
        }

    def format_project(self, pid: str) -> str:
        b = self.get_project_board(pid)
        if not b:
            return "❌ Проект не найден"
        archived = " (АРХИВ)" if b["archived"] else ""
        lines = [f"📁 **{b['name']}**{archived}\n"]
        for label, cards in b["columns"].items():
            lines.append(f"\n**{label}** ({len(cards)})")
            for c in cards[:8]:
                lines.append(f"  • {c['title']}")
        return "\n".join(lines)
