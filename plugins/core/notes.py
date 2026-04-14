"""
plugins/core/notes.py — Локальные заметки с тегами и поиском.

API:
    plugin = NotesPlugin()
    nid = plugin.create("Идея проекта", "...текст...", tags=["работа", "идеи"])
    found = plugin.search("проект")
    by_tag = plugin.list_by_tag("идеи")
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from plugins import CorePlugin


class NotesPlugin(CorePlugin):
    name = "notes"

    def __init__(self) -> None:
        super().__init__()
        self._notes: dict = self.get_json("notes.json", default={})

    def _save(self) -> None:
        self.save_json("notes.json", self._notes)

    def create(self, title: str, text: str = "",
               tags: Optional[list[str]] = None) -> str:
        nid = str(uuid4())[:8]
        self._notes[nid] = {
            "id": nid,
            "title": title,
            "text": text,
            "tags": tags or [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self._save()
        return nid

    def update(self, nid: str, **fields) -> bool:
        if nid not in self._notes:
            return False
        self._notes[nid].update(fields)
        self._notes[nid]["updated_at"] = datetime.now().isoformat()
        self._save()
        return True

    def delete(self, nid: str) -> bool:
        if nid in self._notes:
            del self._notes[nid]
            self._save()
            return True
        return False

    def get(self, nid: str) -> Optional[dict]:
        return self._notes.get(nid)

    def search(self, query: str) -> list[dict]:
        q = query.lower().strip()
        if not q:
            return []
        result = []
        for note in self._notes.values():
            if (q in note["title"].lower() or
                q in note["text"].lower() or
                any(q in t.lower() for t in note.get("tags", []))):
                result.append(note)
        return sorted(result, key=lambda n: n["updated_at"], reverse=True)

    def list_by_tag(self, tag: str) -> list[dict]:
        tag = tag.lower()
        return [n for n in self._notes.values()
                if tag in (t.lower() for t in n.get("tags", []))]

    def list_recent(self, limit: int = 10) -> list[dict]:
        items = sorted(self._notes.values(),
                       key=lambda n: n["updated_at"], reverse=True)
        return items[:limit]

    def all_tags(self) -> list[str]:
        tags = set()
        for n in self._notes.values():
            tags.update(n.get("tags", []))
        return sorted(tags)

    def format_search(self, query: str) -> str:
        results = self.search(query)
        if not results:
            return f"📝 По запросу «{query}» ничего не найдено."
        lines = [f"📝 **Найдено: {len(results)}**\n"]
        for n in results[:10]:
            tags_str = " ".join(f"#{t}" for t in n.get("tags", [])[:3])
            lines.append(f"• **{n['title']}** {tags_str}")
            if n.get("text"):
                lines.append(f"  {n['text'][:80]}…")
        return "\n".join(lines)
