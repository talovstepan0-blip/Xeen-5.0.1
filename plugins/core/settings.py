"""
plugins/core/settings.py — Глобальные настройки Сиен 3.0.0.

Хранит:
  • Личные предпочтения (имя, город, язык, часовой пояс)
  • Настройки UI (тема, акцентный цвет, размер шрифта)
  • Настройки LLM (модель Ollama, base_url, температура)
  • API-ключи для интеграций (Telegram, OpenWeather, и т.д. — лучше через Кронос!)
  • Настройки почты (по умолчанию пусто, актуальные данные в Кроносе)

API:
    s = SettingsPlugin()
    city = s.get("city", "Riga")
    s.set("city", "Moscow")
    s.set_many({"theme": "dark", "lang": "ru"})
    all_settings = s.all()
"""

from __future__ import annotations

from typing import Any

from plugins import CorePlugin

DEFAULTS: dict[str, Any] = {
    "user": {
        "username": "Пользователь",
        "callname": "Хозяин",
        "city": "Riga",
        "country": "LV",
        "lang": "ru",
        "timezone": "Europe/Riga",
    },
    "ui": {
        "theme": "cyberpunk",
        "accent_color": "#00f5ff",
        "font_size": 14,
        "font": "Exo 2",
    },
    "llm": {
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "llama3.2:3b",
        "temperature": 0.2,
        "timeout_sec": 30,
    },
    "assistant": {
        "name": "Сиен",
        "hotword": "Сиен",
        "voice_engine": "piper",
        "context_messages": 10,
        "default_style": "friendly",   # friendly | formal | minimal
    },
    "integrations": {
        "openweather_api_key": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    },
}


class SettingsPlugin(CorePlugin):
    name = "settings"

    def __init__(self) -> None:
        super().__init__()
        loaded = self.get_json("settings.json", default={})
        # Слияние с дефолтами (рекурсивное на 1 уровне)
        self._data: dict[str, Any] = {}
        for key, val in DEFAULTS.items():
            if isinstance(val, dict):
                merged = dict(val)
                merged.update(loaded.get(key, {}))
                self._data[key] = merged
            else:
                self._data[key] = loaded.get(key, val)
        # Дополнительные ключи, которых нет в дефолтах
        for key, val in loaded.items():
            if key not in self._data:
                self._data[key] = val
        self._save()

    def _save(self) -> None:
        self.save_json("settings.json", self._data)

    def get(self, key: str, default: Any = None) -> Any:
        """Поддерживает dotted-keys: 'user.city', 'llm.model'."""
        if "." in key:
            section, name = key.split(".", 1)
            return self._data.get(section, {}).get(name, default)
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if "." in key:
            section, name = key.split(".", 1)
            if section not in self._data or not isinstance(self._data[section], dict):
                self._data[section] = {}
            self._data[section][name] = value
        else:
            self._data[key] = value
        self._save()

    def set_many(self, items: dict[str, Any]) -> None:
        for k, v in items.items():
            self.set(k, v)

    def reset_section(self, section: str) -> None:
        if section in DEFAULTS:
            self._data[section] = dict(DEFAULTS[section])
            self._save()

    def reset_all(self) -> None:
        self._data = {k: dict(v) if isinstance(v, dict) else v
                      for k, v in DEFAULTS.items()}
        self._save()

    def all(self) -> dict:
        return self._data
