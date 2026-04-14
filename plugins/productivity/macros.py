"""
plugins/productivity/macros.py — Макросы команд.

Макрос — это последовательность команд с задержками между ними.
Запускается в отдельном потоке через переданный handler.

API:
    m = MacrosPlugin()
    mid = m.create("Утро", [
        {"command": "погода", "delay_sec": 0},
        {"command": "новости технологии", "delay_sec": 2},
        {"command": "мои задачи", "delay_sec": 2},
    ])
    m.run("Утро", handler=lambda cmd: process(cmd))
    m.delete("Утро")
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from plugins import CorePlugin

logger = logging.getLogger("plugins.macros")


class MacrosPlugin(CorePlugin):
    name = "macros"

    def __init__(self) -> None:
        super().__init__()
        self._macros: dict = self.get_json("macros.json", default={})
        self._running: dict[str, threading.Thread] = {}

    def _save(self) -> None:
        self.save_json("macros.json", self._macros)

    def create(self, name: str, steps: list[dict]) -> bool:
        """
        steps = [{"command": "...", "delay_sec": 0}, ...]
        """
        normalized = []
        for s in steps:
            if not isinstance(s, dict) or "command" not in s:
                continue
            normalized.append({
                "command": str(s["command"]),
                "delay_sec": float(s.get("delay_sec", 0)),
            })
        if not normalized:
            return False
        self._macros[name] = {
            "name": name,
            "steps": normalized,
            "runs_count": 0,
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        return True

    def delete(self, name: str) -> bool:
        if name in self._macros:
            del self._macros[name]
            self._save()
            return True
        return False

    def list_all(self) -> list[dict]:
        return list(self._macros.values())

    def get(self, name: str) -> dict | None:
        return self._macros.get(name)

    def run(self, name: str, handler: Callable[[str], None]) -> bool:
        """
        Запускает макрос в отдельном потоке.
        handler(command_text) — функция обработки одной команды.
        """
        macro = self._macros.get(name)
        if not macro:
            return False

        if name in self._running and self._running[name].is_alive():
            logger.warning(f"Макрос '{name}' уже выполняется")
            return False

        def _exec():
            for step in macro["steps"]:
                if step["delay_sec"] > 0:
                    time.sleep(step["delay_sec"])
                try:
                    handler(step["command"])
                except Exception as e:
                    logger.error(f"Шаг макроса '{name}': {e}")
            macro["runs_count"] = macro.get("runs_count", 0) + 1
            macro["last_run_at"] = datetime.now().isoformat()
            self._save()

        thread = threading.Thread(target=_exec, daemon=True,
                                  name=f"Macro-{name}")
        thread.start()
        self._running[name] = thread
        return True

    def is_running(self, name: str) -> bool:
        t = self._running.get(name)
        return bool(t and t.is_alive())

    def stats(self) -> dict:
        return {
            "total": len(self._macros),
            "running": sum(1 for n in self._running if self.is_running(n)),
            "macros": [
                {"name": m["name"],
                 "steps": len(m["steps"]),
                 "runs": m.get("runs_count", 0)}
                for m in self._macros.values()
            ],
        }
