"""
plugins/core/news.py — Новости из RSS-лент.

Источники: Habr, 3DNews, BBC Russian, N+1.
Всё бесплатно, без API-ключей.

API:
    plugin = NewsPlugin()
    items = plugin.get_news("tech", limit=10)
    items = plugin.get_news("world", limit=5)
"""

from __future__ import annotations

import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from plugins import CorePlugin

logger = logging.getLogger("plugins.news")

CACHE_TTL_SEC = 600  # 10 минут

# Категория → список RSS-лент
FEEDS: dict[str, list[str]] = {
    "tech": [
        "https://habr.com/ru/rss/best/",
        "https://3dnews.ru/news/rss/",
    ],
    "science": [
        "https://nplus1.ru/rss",
    ],
    "world": [
        "https://feeds.bbci.co.uk/russian/rss.xml",
    ],
    "all": [
        "https://habr.com/ru/rss/best/",
        "https://3dnews.ru/news/rss/",
        "https://nplus1.ru/rss",
        "https://feeds.bbci.co.uk/russian/rss.xml",
    ],
}


class NewsPlugin(CorePlugin):
    name = "news"

    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def _fetch_feed(self, url: str) -> list[dict]:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Sien/3.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
            return self._parse_rss(raw, url)
        except Exception as e:
            logger.warning(f"RSS {url}: {e}")
            return []

    def _parse_rss(self, raw: bytes, source: str) -> list[dict]:
        items = []
        try:
            root = ET.fromstring(raw)
            # RSS 2.0
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                desc = (item.findtext("description") or "").strip()
                # Чистим HTML
                import re
                desc = re.sub(r"<[^>]+>", "", desc)[:200]
                if title:
                    items.append({
                        "title": title,
                        "link": link,
                        "pub_date": pub,
                        "description": desc,
                        "source": source,
                    })
            # Atom (для запасных лент)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title = entry.findtext("atom:title", "", ns).strip()
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href") if link_el is not None else ""
                if title:
                    items.append({
                        "title": title, "link": link,
                        "pub_date": entry.findtext("atom:updated", "", ns),
                        "description": "", "source": source,
                    })
        except ET.ParseError as e:
            logger.warning(f"XML parse {source}: {e}")
        return items

    def get_news(self, category: str = "all", limit: int = 10) -> list[dict]:
        cat = category.lower()
        if cat not in FEEDS:
            cat = "all"

        cache_key = f"{cat}::{limit}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < CACHE_TTL_SEC:
            return cached[1]

        all_items: list[dict] = []
        for feed_url in FEEDS[cat]:
            all_items.extend(self._fetch_feed(feed_url))

        # Уникализация по заголовку
        seen = set()
        unique: list[dict] = []
        for it in all_items:
            if it["title"] not in seen:
                seen.add(it["title"])
                unique.append(it)
        result = unique[:limit]
        self._cache[cache_key] = (time.time(), result)
        return result

    def format_news(self, category: str = "all", limit: int = 5) -> str:
        items = self.get_news(category, limit)
        if not items:
            return "📰 Новости недоступны (нет связи с RSS-лентами)."

        cat_label = {
            "tech": "Технологии", "science": "Наука",
            "world": "Мир", "all": "Все новости",
        }.get(category, category)

        lines = [f"📰 **{cat_label}**\n"]
        for i, n in enumerate(items, 1):
            lines.append(f"{i}. **{n['title']}**")
            if n.get("description"):
                lines.append(f"   {n['description'][:140]}…")
            if n.get("link"):
                lines.append(f"   🔗 {n['link']}")
            lines.append("")
        return "\n".join(lines)

    def list_categories(self) -> list[str]:
        return list(FEEDS.keys())
