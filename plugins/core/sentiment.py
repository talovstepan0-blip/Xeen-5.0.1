"""
plugins/core/sentiment.py — Анализ тональности (positive/negative/neutral).

Не путать с core/emotion.py — там 5 эмоций, тут 3 polarity.
Используется для аналитики комментариев, отзывов, истории команд.

API:
    s = SentimentAnalyzer()
    polarity = s.analyze("отлично, спасибо!")    # "positive"
    polarity = s.analyze("ужасно, не работает")  # "negative"
    score = s.score("текст")                      # -1.0..+1.0
"""

from __future__ import annotations

import re
from typing import Literal

Polarity = Literal["positive", "negative", "neutral"]

POSITIVE_WORDS = {
    "хорошо", "отлично", "великолепно", "прекрасно", "супер", "класс",
    "круто", "замечательно", "восхитительно", "потрясающе", "удобно",
    "красиво", "приятно", "спасибо", "благодарю", "доволен", "довольна",
    "люблю", "нравится", "понравилось", "рекомендую", "идеально",
    "быстро", "качественно", "лучше", "лучший", "позитивно", "ура",
    "good", "great", "awesome", "excellent", "amazing", "wonderful",
    "love", "perfect", "best", "happy", "nice",
}

NEGATIVE_WORDS = {
    "плохо", "ужасно", "отвратительно", "кошмар", "хуже", "худший",
    "неприятно", "недоволен", "недовольна", "ненавижу", "не нравится",
    "медленно", "тормозит", "сломано", "не работает", "ошибка", "баг",
    "разочарован", "разочарована", "обман", "фигня", "грустно",
    "проблема", "дорого", "сложно", "невозможно", "минус", "негативно",
    "bad", "terrible", "awful", "worst", "hate", "broken", "slow",
    "buggy", "disappointing", "useless", "annoying",
}

NEGATIONS = {"не", "ни", "нет", "никак", "никогда", "не_было"}

TOKEN_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)


class SentimentAnalyzer:
    def __init__(self,
                 positive: set[str] = POSITIVE_WORDS,
                 negative: set[str] = NEGATIVE_WORDS):
        self.positive = positive
        self.negative = negative

    def _tokenize(self, text: str) -> list[str]:
        return [t.lower() for t in TOKEN_RE.findall(text or "")]

    def analyze(self, text: str) -> Polarity:
        s = self.score(text)
        if s > 0.15:
            return "positive"
        if s < -0.15:
            return "negative"
        return "neutral"

    def score(self, text: str) -> float:
        """Оценка тональности от -1.0 до +1.0."""
        if not text:
            return 0.0
        tokens = self._tokenize(text)
        if not tokens:
            return 0.0

        pos = neg = 0
        for i, t in enumerate(tokens):
            # Проверяем отрицание перед словом
            negated = i > 0 and tokens[i - 1] in NEGATIONS
            if t in self.positive:
                if negated:
                    neg += 1
                else:
                    pos += 1
            elif t in self.negative:
                if negated:
                    pos += 1
                else:
                    neg += 1

        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total

    def explain(self, text: str) -> dict:
        """Возвращает детальный разбор тональности."""
        tokens = self._tokenize(text)
        found_pos = [t for t in tokens if t in self.positive]
        found_neg = [t for t in tokens if t in self.negative]
        sc = self.score(text)
        return {
            "polarity": self.analyze(text),
            "score": round(sc, 3),
            "positive_words": found_pos,
            "negative_words": found_neg,
            "tokens_count": len(tokens),
        }
