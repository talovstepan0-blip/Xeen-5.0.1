"""
plugins/productivity/focus_mode.py — Режим фокусировки.

Блокирует отвлекающие команды (по списку ключевых слов).
По умолчанию блокируются: музыка, видео, новости, развлечения.

API:
    f = FocusMode()
    f.enable(duration_minutes=60)
    blocked = f.is_blocked("включи музыку")   # True
    f.disable()
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from plugins import CorePlugin

DEFAULT_BLOCKED = [
    "музыка", "music", "плейлист", "spotify", "youtube", "ютуб",
    "видео", "рутуб", "rutube", "vk видео", "tiktok",
    "новости", "news", "развлечение", "instagram", "twitter",
    "игра", "game", "стрим", "twitch",
]


class FocusMode(CorePlugin):
    name = "focus_mode"

    def __init__(self) -> None:
        super().__init__()
        state = self.get_json("state.json", default={})
        self._enabled: bool = state.get("enabled", False)
        self._until: Optional[str] = state.get("until")
        self._blocked_words: list[str] = state.get("blocked_words", DEFAULT_BLOCKED)
        self._sessions: list[dict] = state.get("sessions", [])

    def _save(self) -> None:
        self.save_json("state.json", {
            "enabled": self._enabled,
            "until": self._until,
            "blocked_words": self._blocked_words,
            "sessions": self._sessions[-50:],  # последние 50 сессий
        })

    def enable(self, duration_minutes: int = 60) -> dict:
        self._enabled = True
        self._until = (datetime.now() + timedelta(minutes=duration_minutes)).isoformat()
        self._sessions.append({
            "started_at": datetime.now().isoformat(),
            "duration_minutes": duration_minutes,
        })
        self._save()
        return {
            "enabled": True,
            "until": self._until,
            "duration_minutes": duration_minutes,
        }

    def disable(self) -> None:
        self._enabled = False
        self._until = None
        self._save()

    def _check_expiry(self) -> None:
        if self._enabled and self._until:
            try:
                if datetime.now() > datetime.fromisoformat(self._until):
                    self.disable()
            except ValueError:
                pass

    def is_active(self) -> bool:
        self._check_expiry()
        return self._enabled

    def is_blocked(self, command: str) -> bool:
        if not self.is_active():
            return False
        import re
        cmd_lower = command.lower()
        # Извлекаем токены и проверяем каждое блок-слово как префикс токена.
        # Так "музыка" заматчит и "музыку", и "музыкой", и "музыкальный".
        tokens = re.findall(r"[а-яёa-z0-9]+", cmd_lower)
        for w in self._blocked_words:
            w = w.strip().lower()
            if not w:
                continue
            # Если в блок-слове несколько токенов (например "vk видео") — обычное вхождение
            if " " in w:
                if w in cmd_lower:
                    return True
                continue
            # Иначе — корневая проверка: токен начинается с блок-слова первых ~5 символов
            stem = w[:5] if len(w) >= 5 else w
            for t in tokens:
                if t.startswith(stem):
                    return True
        return False

    def add_blocked_word(self, word: str) -> None:
        word = word.lower().strip()
        if word and word not in self._blocked_words:
            self._blocked_words.append(word)
            self._save()

    def remove_blocked_word(self, word: str) -> bool:
        word = word.lower().strip()
        if word in self._blocked_words:
            self._blocked_words.remove(word)
            self._save()
            return True
        return False

    def status(self) -> dict:
        self._check_expiry()
        remaining = 0
        if self._enabled and self._until:
            try:
                until_dt = datetime.fromisoformat(self._until)
                remaining = max(0, int((until_dt - datetime.now()).total_seconds() / 60))
            except ValueError:
                pass
        return {
            "enabled": self._enabled,
            "until": self._until,
            "remaining_minutes": remaining,
            "blocked_words_count": len(self._blocked_words),
            "total_sessions": len(self._sessions),
        }
