"""
plugins/productivity/kanban.py — Канбан-доска (одна общая, три колонки).

Колонки: todo (К выполнению), progress (В работе), done (Готово).
Для управления отдельными проектами используй project_kanban.py.

API:
    k = KanbanPlugin()
    cid = k.add("Написать отчёт", "todo")
    k.move(cid, "progress")
    k.move(cid, "done")
    board = k.get_board()
    k.delete(cid)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin

COLUMNS = ("todo", "progress", "done")
COLUMN_LABELS = {
    "todo": "К выполнению",
    "progress": "В работе",
    "done": "Готово",
}


class KanbanPlugin(CorePlugin):
    name = "kanban"

    def __init__(self) -> None:
        super().__init__()
        self._board: dict = self.get_json("board.json", default={c: [] for c in COLUMNS})
        # Гарантируем, что все колонки существуют
        for c in COLUMNS:
            self._board.setdefault(c, [])

    def _save(self) -> None:
        self.save_json("board.json", self._board)

    def add(self, title: str, column: str = "todo") -> str:
        if column not in COLUMNS:
            column = "todo"
        cid = str(uuid4())[:8]
        self._board[column].append({
            "id": cid,
            "title": title,
            "created_at": datetime.now().isoformat(),
            "moved_at": datetime.now().isoformat(),
        })
        self._save()
        return cid

    def move(self, cid: str, to_column: str) -> bool:
        if to_column not in COLUMNS:
            return False
        for col in COLUMNS:
            for idx, card in enumerate(self._board[col]):
                if card["id"] == cid:
                    if col == to_column:
                        return True
                    card = self._board[col].pop(idx)
                    card["moved_at"] = datetime.now().isoformat()
                    self._board[to_column].append(card)
                    self._save()
                    return True
        return False

    def delete(self, cid: str) -> bool:
        for col in COLUMNS:
            for idx, card in enumerate(self._board[col]):
                if card["id"] == cid:
                    self._board[col].pop(idx)
                    self._save()
                    return True
        return False

    def get_board(self) -> dict:
        return {COLUMN_LABELS[c]: list(self._board.get(c, [])) for c in COLUMNS}

    def get_column(self, column: str) -> list[dict]:
        return list(self._board.get(column, []))

    def stats(self) -> dict:
        return {COLUMN_LABELS[c]: len(self._board.get(c, [])) for c in COLUMNS}

    def format_board(self) -> str:
        lines = ["📋 **Канбан-доска:**\n"]
        for col in COLUMNS:
            label = COLUMN_LABELS[col]
            cards = self._board.get(col, [])
            lines.append(f"\n**{label}** ({len(cards)})")
            for c in cards[:10]:
                lines.append(f"  • {c['title']}")
        return "\n".join(lines)
