"""
core/emotion.py — Эмоциональный анализ текста (rule-based).

Возвращает одну из меток:
  joy, sadness, anger, fear, neutral

Rule-based словарь работает офлайн и не требует моделей.
При наличии transformers + rubert-tiny2 можно подключить ML-бэкенд.
"""

from __future__ import annotations

import re
from typing import Literal

Emotion = Literal["joy", "sadness", "anger", "fear", "neutral"]

# Словари маркеров (нижний регистр, без знаков)
_JOY = {
    "рад", "радость", "счастлив", "счастье", "ура", "супер", "класс", "круто",
    "отлично", "великолепно", "прекрасно", "доволен", "здорово", "кайф",
    "восхитительно", "замечательно", "спасибо", "благодарю", "люблю", "любимый",
    "happy", "joy", "great", "awesome", "love", "thanks",
}
_SADNESS = {
    "грустно", "грусть", "печаль", "печально", "плохо", "тоска", "одинок",
    "одиноко", "устал", "устала", "разочарован", "жаль", "жалко", "плачу",
    "расстроен", "депрессия", "тяжело", "больно", "страдаю",
    "sad", "tired", "cry", "hurt", "lonely", "depressed",
}
_ANGER = {
    "злюсь", "злой", "злая", "бесит", "бесишь", "ненавижу", "ярость", "гнев",
    "раздражает", "достало", "достал", "ужасно", "отвратительно", "хватит",
    "надоело", "заткнись", "идиот", "дурак", "тупой",
    "angry", "hate", "mad", "furious", "stupid", "annoying",
}
_FEAR = {
    "боюсь", "страшно", "страх", "паника", "тревога", "беспокоюсь",
    "волнуюсь", "опасно", "опасаюсь", "испугался", "испугалась", "ужас",
    "кошмар", "нервничаю",
    "afraid", "scared", "fear", "panic", "anxiety", "worried", "nervous",
}

_TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _TOKEN_RE.findall(text or "")]


def analyze_emotion(text: str) -> Emotion:
    """Определяет доминирующую эмоцию в тексте."""
    if not text:
        return "neutral"
    tokens = _tokenize(text)
    if not tokens:
        return "neutral"

    scores = {
        "joy":     sum(1 for t in tokens if t in _JOY),
        "sadness": sum(1 for t in tokens if t in _SADNESS),
        "anger":   sum(1 for t in tokens if t in _ANGER),
        "fear":    sum(1 for t in tokens if t in _FEAR),
    }

    # Учитываем пунктуацию: много "!" усиливает гнев/радость; "?" — тревогу
    exclam = text.count("!")
    question = text.count("?")
    if exclam >= 3:
        scores["anger"] += 1
        scores["joy"] += 1
    if question >= 2:
        scores["fear"] += 1

    max_label = max(scores.items(), key=lambda kv: kv[1])
    if max_label[1] == 0:
        return "neutral"
    return max_label[0]  # type: ignore[return-value]


def emotion_emoji(emotion: str) -> str:
    return {
        "joy":     "😊",
        "sadness": "😔",
        "anger":   "😠",
        "fear":    "😨",
        "neutral": "😐",
    }.get(emotion, "😐")
