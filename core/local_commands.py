"""
core/local_commands.py — Локальные команды Сиен 3.0.0.

Исправления против 2.0 Beta:
  • Путь к local_commands.yaml ищется в нескольких местах (корень, config/, core/).
  • Экспортирована функция match_command(text) как простой API для dashboard.
  • Singleton-движок инициализируется лениво.
  • Понимает мультиязычные фразы и работает <50ms.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("local_commands")

# ── Поиск конфигурации ────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

_YAML_CANDIDATES = [
    BASE_DIR / "config" / "local_commands.yaml",
    BASE_DIR / "local_commands.yaml",
    BASE_DIR / "core" / "local_commands.yaml",
]
_SYNTH_CANDIDATES = [
    BASE_DIR / "generated_synonyms.json",
    BASE_DIR / "data" / "generated_synonyms.json",
]


def _find_file(candidates: list[Path]) -> Optional[Path]:
    for c in candidates:
        if c.exists():
            return c
    return None


# ── Константы ─────────────────────────────────────────────────────
AGENT_NAMES = {
    "аполлон": "apollo", "apollo": "apollo",
    "гермес": "hermes", "hermes": "hermes",
    "вэнь": "wen", "вэн": "wen", "wen": "wen",
    "ахилл": "ahill", "ahill": "ahill",
    "плутос": "plutos", "plutos": "plutos",
    "кронос": "cronos", "cronos": "cronos",
    "аргус": "argus", "argus": "argus",
    "логос": "logos", "logos": "logos",
    "кун": "kun", "kun": "kun",
    "мастер": "master", "master": "master",
    "муса": "musa", "musa": "musa",
    "каллио": "kallio", "kallio": "kallio",
    "гефест": "hefest", "hefest": "hefest",
    "авто": "avto", "avto": "avto",
    "хуэй": "huei", "huei": "huei",
    "мэн": "meng", "meng": "meng",
    "эхо": "eho", "eho": "eho",
    "ирида": "irida", "irida": "irida",
    "феникс": "fenix", "fenix": "fenix",
    "мнемон": "mnemon", "mnemon": "mnemon",
    "дике": "dike", "dike": "dike",
}


@dataclass
class CommandMatch:
    command_name: str
    params: dict[str, Any]
    confidence: float
    matched_pattern: str = ""
    processing_ms: float = 0.0


# ══════════════════════════════════════════════════════════════════
# Engine
# ══════════════════════════════════════════════════════════════════

class LocalCommandsEngine:
    def __init__(self) -> None:
        self._commands: dict[str, dict] = {}
        self._patterns: list[tuple[re.Pattern, str, float]] = []
        self._keyword_index: dict[str, list[str]] = {}
        self._loaded = False
        self.load()

    def load(self) -> None:
        yaml_path = _find_file(_YAML_CANDIDATES)
        if yaml_path is None:
            logger.warning("local_commands.yaml не найден ни в одной из стандартных директорий")
            self._loaded = False
            return

        try:
            import yaml
        except ImportError:
            logger.error("pyyaml не установлен")
            self._loaded = False
            return

        t0 = time.monotonic()
        try:
            with open(yaml_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Ошибка чтения {yaml_path}: {e}")
            self._loaded = False
            return

        commands_raw: dict = raw.get("commands", {})

        generated: dict[str, list[str]] = {}
        synth_path = _find_file(_SYNTH_CANDIDATES)
        if synth_path:
            try:
                generated = json.loads(synth_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Синонимы {synth_path}: {e}")

        self._commands.clear()
        self._patterns.clear()
        self._keyword_index.clear()

        for cmd_name, cmd_cfg in commands_raw.items():
            if not isinstance(cmd_cfg, dict):
                continue
            if not cmd_cfg.get("enabled", True):
                continue
            self._commands[cmd_name] = cmd_cfg

            phrases: list[str] = list(cmd_cfg.get("phrases", []))
            phrases += generated.get(cmd_name, [])
            phrases = list(dict.fromkeys(p.lower().strip() for p in phrases if p))

            for phrase in phrases:
                try:
                    pat = re.compile(re.escape(phrase), re.IGNORECASE | re.UNICODE)
                    self._patterns.append((pat, cmd_name, 1.0))
                except re.error:
                    pass

            for kw in cmd_cfg.get("keywords", []):
                self._keyword_index.setdefault(kw.lower(), []).append(cmd_name)

            for regex_str in cmd_cfg.get("regex_patterns", []):
                try:
                    pat = re.compile(regex_str, re.IGNORECASE | re.UNICODE)
                    self._patterns.append((pat, cmd_name, 0.95))
                except re.error as e:
                    logger.warning(f"Плохой regex {cmd_name}: {regex_str} — {e}")

        self._loaded = True
        elapsed = (time.monotonic() - t0) * 1000
        logger.info(
            f"LocalCommands загружен: {len(self._commands)} команд, "
            f"{len(self._patterns)} паттернов за {elapsed:.1f}ms"
        )

    def reload(self) -> None:
        self.load()

    def match(self, text: str) -> Optional[CommandMatch]:
        t0 = time.monotonic()
        if not self._loaded or not text.strip():
            return None

        text_clean = text.strip().lower()

        # Точное совпадение
        for pat, cmd_name, weight in self._patterns:
            if pat.fullmatch(text_clean):
                return CommandMatch(
                    command_name=cmd_name,
                    params=self._extract_params(text_clean, cmd_name),
                    confidence=weight,
                    matched_pattern=pat.pattern,
                    processing_ms=(time.monotonic() - t0) * 1000,
                )

        # Частичное — поиск подстроки (regex search)
        for pat, cmd_name, weight in self._patterns:
            if pat.search(text_clean):
                return CommandMatch(
                    command_name=cmd_name,
                    params=self._extract_params(text_clean, cmd_name),
                    confidence=weight * 0.85,
                    matched_pattern=pat.pattern,
                    processing_ms=(time.monotonic() - t0) * 1000,
                )

        # По ключевым словам
        tokens = set(re.findall(r"[а-яёa-z0-9]+", text_clean))
        best: tuple[Optional[str], int] = (None, 0)
        for kw, cmd_names in self._keyword_index.items():
            if kw in tokens:
                for cmd in cmd_names:
                    if cmd == best[0]:
                        best = (cmd, best[1] + 1)
                    elif best[1] == 0:
                        best = (cmd, 1)
        if best[0]:
            return CommandMatch(
                command_name=best[0],
                params=self._extract_params(text_clean, best[0]),
                confidence=0.6,
                matched_pattern="keyword",
                processing_ms=(time.monotonic() - t0) * 1000,
            )

        return None

    def _extract_params(self, text: str, cmd_name: str) -> dict[str, Any]:
        cfg = self._commands.get(cmd_name, {})
        params: dict[str, Any] = {}

        # Агент
        for word in text.split():
            if word in AGENT_NAMES:
                params["agent"] = AGENT_NAMES[word]
                break

        # Числа
        nums = re.findall(r"\b\d+(?:[.,]\d+)?\b", text)
        if nums:
            params["numbers"] = [float(n.replace(",", ".")) for n in nums]

        # Дополнительные из конфига (slot_patterns: { slot_name: regex })
        for slot, regex in cfg.get("slot_patterns", {}).items():
            m = re.search(regex, text, re.IGNORECASE | re.UNICODE)
            if m:
                params[slot] = m.group(1) if m.groups() else m.group(0)

        return params

    def list_commands(self) -> list[str]:
        return list(self._commands.keys())


# ══════════════════════════════════════════════════════════════════
# Singleton + публичный API
# ══════════════════════════════════════════════════════════════════

_engine: Optional[LocalCommandsEngine] = None


def get_engine() -> LocalCommandsEngine:
    global _engine
    if _engine is None:
        _engine = LocalCommandsEngine()
    return _engine


def match_command(text: str) -> tuple[Optional[str], dict, float]:
    """
    Главная функция для dashboard.py и внешних вызовов.

    Возвращает кортеж: (command_name|None, params, confidence).
    """
    m = get_engine().match(text)
    if m is None:
        return (None, {}, 0.0)
    return (m.command_name, m.params, m.confidence)


# Обратная совместимость со старым API
def match_local_command(text: str) -> tuple[Optional[str], dict, float]:
    return match_command(text)
