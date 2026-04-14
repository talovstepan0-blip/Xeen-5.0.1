"""
plugins/ — Плагины Сиен 3.0.0.

Базовый класс CorePlugin:
  • storage_path → Path в data/plugins/<n>/
  • get_json / save_json — атомарная работа с JSON-файлами плагина
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("plugins")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "plugins"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class CorePlugin:
    """Базовый класс для всех плагинов."""
    name: str = "base"

    def __init__(self) -> None:
        self.storage_path = DATA_DIR / self.name
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def file(self, fname: str) -> Path:
        return self.storage_path / fname

    def get_json(self, fname: str, default: Any = None) -> Any:
        p = self.file(fname)
        if not p.exists():
            return default if default is not None else {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"{self.name}/{fname}: {e}")
            return default if default is not None else {}

    def save_json(self, fname: str, data: Any) -> None:
        p = self.file(fname)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False,
                dir=str(self.storage_path), suffix=".tmp"
            ) as tmp:
                json.dump(data, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, p)
        except Exception as e:
            logger.error(f"{self.name}/{fname}: {e}")
