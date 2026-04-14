"""
plugins/core/weather.py — Погода через OpenWeatherMap.

API:
    plugin = WeatherPlugin()
    current = plugin.get_current("Riga")           # текущая
    forecast = plugin.get_forecast("Moscow", days=5)

Конфигурация:
    Ключ берётся из:
    1) plugin.set_api_key("...")
    2) переменной окружения OPENWEATHER_API_KEY
    3) data/plugins/weather/config.json {"api_key": "..."}

Кэш: 5 минут (TTL).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional
from urllib.parse import quote

from plugins import CorePlugin

logger = logging.getLogger("plugins.weather")

CACHE_TTL_SEC = 300
BASE_URL = "https://api.openweathermap.org/data/2.5"


class WeatherPlugin(CorePlugin):
    name = "weather"

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._config = self.get_json("config.json", default={})

    # ── API key ──────────────────────────────────────────────────

    def get_api_key(self) -> Optional[str]:
        return (
            self._config.get("api_key")
            or os.environ.get("OPENWEATHER_API_KEY")
            or None
        )

    def set_api_key(self, key: str) -> None:
        self._config["api_key"] = key
        self.save_json("config.json", self._config)

    # ── Кэш ──────────────────────────────────────────────────────

    def _cache_get(self, key: str) -> Optional[dict]:
        item = self._cache.get(key)
        if not item:
            return None
        ts, data = item
        if time.time() - ts > CACHE_TTL_SEC:
            self._cache.pop(key, None)
            return None
        return data

    def _cache_set(self, key: str, data: dict) -> None:
        self._cache[key] = (time.time(), data)

    # ── HTTP ─────────────────────────────────────────────────────

    def _fetch(self, path: str, **params) -> dict:
        api_key = self.get_api_key()
        if not api_key:
            return {"error": "API-ключ OpenWeatherMap не настроен"}

        params["appid"] = api_key
        params.setdefault("units", "metric")
        params.setdefault("lang", "ru")

        import urllib.request
        import urllib.parse
        import json as _json

        url = f"{BASE_URL}/{path}?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return _json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}

    # ── Публичные методы ────────────────────────────────────────

    def get_current(self, city: str) -> dict:
        key = f"current::{city.lower()}"
        cached = self._cache_get(key)
        if cached is not None:
            return {**cached, "_cached": True}

        data = self._fetch("weather", q=city)
        if "error" in data:
            return data

        result = {
            "city": data.get("name"),
            "country": data.get("sys", {}).get("country"),
            "temp": round(data.get("main", {}).get("temp", 0), 1),
            "feels_like": round(data.get("main", {}).get("feels_like", 0), 1),
            "description": (data.get("weather") or [{}])[0].get("description", ""),
            "humidity": data.get("main", {}).get("humidity"),
            "wind_speed": data.get("wind", {}).get("speed"),
            "pressure": data.get("main", {}).get("pressure"),
        }
        self._cache_set(key, result)
        return result

    def get_forecast(self, city: str, days: int = 5) -> dict:
        key = f"forecast::{city.lower()}::{days}"
        cached = self._cache_get(key)
        if cached is not None:
            return {**cached, "_cached": True}

        data = self._fetch("forecast", q=city, cnt=min(days * 8, 40))
        if "error" in data:
            return data

        # Группируем по дням
        by_day: dict[str, list] = {}
        for item in data.get("list", []):
            day = item.get("dt_txt", "")[:10]
            by_day.setdefault(day, []).append(item)

        forecast = []
        for day, items in list(by_day.items())[:days]:
            temps = [it["main"]["temp"] for it in items if "main" in it]
            descs = [(it["weather"] or [{}])[0].get("description", "") for it in items]
            forecast.append({
                "date": day,
                "temp_min": round(min(temps), 1) if temps else None,
                "temp_max": round(max(temps), 1) if temps else None,
                "description": max(set(descs), key=descs.count) if descs else "",
            })

        result = {
            "city": data.get("city", {}).get("name"),
            "days": forecast,
        }
        self._cache_set(key, result)
        return result

    def format_current(self, city: str) -> str:
        d = self.get_current(city)
        if "error" in d:
            return f"❌ Погода: {d['error']}"
        return (
            f"🌤 **{d['city']}** ({d.get('country', '?')})\n"
            f"• Температура: **{d['temp']}°C** (ощущается {d['feels_like']}°C)\n"
            f"• {d['description'].capitalize()}\n"
            f"• Влажность: {d['humidity']}%, ветер: {d['wind_speed']} м/с\n"
            f"• Давление: {d['pressure']} гПа"
        )
