"""
plugins/core/logger.py — Логгер плагина с записью в файл.

API:
    log = LoggerPlugin()
    log.info("source", "сообщение")
    log.warning("agent", "предупреждение")
    log.error("agent", "ошибка")
    recent = log.tail(50)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from plugins import CorePlugin

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class LoggerPlugin(CorePlugin):
    name = "logger"

    def __init__(self) -> None:
        super().__init__()
        self.log_file: Path = self.storage_path / "events.log"

    def _write(self, level: str, source: str, message: str) -> None:
        if level not in LEVELS:
            level = "INFO"
        ts = datetime.now().isoformat(timespec="seconds")
        line = f"{ts} | {level:<7} | {source:<20} | {message}\n"
        try:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logging.warning(f"LoggerPlugin write: {e}")

    def debug(self, source: str, message: str) -> None:
        self._write("DEBUG", source, message)

    def info(self, source: str, message: str) -> None:
        self._write("INFO", source, message)

    def warning(self, source: str, message: str) -> None:
        self._write("WARNING", source, message)

    def error(self, source: str, message: str) -> None:
        self._write("ERROR", source, message)

    def tail(self, n: int = 50, level: Optional[str] = None) -> list[str]:
        if not self.log_file.exists():
            return []
        try:
            lines = self.log_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        if level:
            lines = [l for l in lines if f"| {level:<7} |" in l]
        return lines[-n:]

    def clear(self) -> None:
        try:
            self.log_file.write_text("", encoding="utf-8")
        except OSError:
            pass

    def stats(self) -> dict:
        if not self.log_file.exists():
            return {"total": 0, "by_level": {}}
        try:
            lines = self.log_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"total": 0, "by_level": {}}
        by_level = {lvl: 0 for lvl in LEVELS}
        for line in lines:
            for lvl in LEVELS:
                if f"| {lvl:<7} |" in line:
                    by_level[lvl] += 1
                    break
        return {"total": len(lines), "by_level": by_level,
                "file_size_bytes": self.log_file.stat().st_size}
