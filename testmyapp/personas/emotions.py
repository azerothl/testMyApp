"""Modèle émotionnel des personas (spec §5).

Humeur ``mood ∈ [−5, +5]`` mise à jour par EMA à partir de la valence des
événements ; émotions ponctuelles distinctes de l'humeur de fond.
"""
from __future__ import annotations

import time

MOOD_MIN, MOOD_MAX = -5.0, 5.0
EMA_ALPHA = 0.3  # spec §5.1

# Valence des événements (table §5.2)
VALENCE = {
    "step_fast_pass": +2.0,
    "step_pass": +1.0,
    "step_slow_pass": -1.0,
    "step_warn": -1.0,
    "step_fail": -2.0,
    "console_error": -1.0,
    "crash": -4.0,
    "a11y_violation": -2.0,
    "curiosity_reward": +2.0,
    "goal_reached": +3.0,
    "abandon": -5.0,
    "long_wait": -1.0,
}

# Émotions ponctuelles (Annexe B)
PUNCTUAL = {
    "celebrate": {"label": "Célébration", "emoji": "🎉"},
    "confus": {"label": "Confus", "emoji": "❓"},
    "surpris": {"label": "Surpris", "emoji": "😮"},
    "fatigue": {"label": "Fatigué", "emoji": "🥱"},
    "impatient": {"label": "Impatient", "emoji": "⏳"},
    "eureka": {"label": "Eurêka", "emoji": "💡"},
    "dort": {"label": "Dort", "emoji": "💤"},
    "abandonne": {"label": "Abandonné", "emoji": "🏳️"},
    "agace": {"label": "Agacé", "emoji": "😒"},
    "furieux": {"label": "Furieux", "emoji": "😡"},
    "joyeux": {"label": "Joyeux", "emoji": "😄"},
    "content": {"label": "Content", "emoji": "🙂"},
    "intrigue": {"label": "Intrigué", "emoji": "🤔"},
}


def mood_badge(mood: float) -> dict:
    """Émotion de fond (badge persistant) selon l'humeur — table §5.3."""
    if mood >= 3:
        return {"id": "joyeux", "label": "Radieux", "emoji": "😄"}
    if mood >= 1:
        return {"id": "content", "label": "Content", "emoji": "🙂"}
    if mood >= -1:
        return {"id": "neutre", "label": "Neutre", "emoji": "😐"}
    if mood >= -3:
        return {"id": "agace", "label": "Agacé", "emoji": "😕"}
    return {"id": "furieux", "label": "Furieux", "emoji": "😠"}


class MoodTracker:
    """EMA de valence pour un persona, avec anti-spam (cooldown 2 s, §5.4)."""

    def __init__(self, bias: float = 0.0, volatility: float = 1.0) -> None:
        self.mood = max(min(bias * MOOD_MAX, MOOD_MAX), MOOD_MIN)
        self.volatility = max(0.3, volatility)
        self._last_emoji_ts = 0.0
        self._pending_merge: tuple[str, int] | None = None  # (emotion, count)

    def apply(self, valence: float) -> float:
        v = valence * self.volatility
        self.mood = max(min(self.mood + EMA_ALPHA * (v - self.mood), MOOD_MAX), MOOD_MIN)
        return self.mood

    def punctual(self, emotion_id: str, now: float | None = None) -> dict | None:
        """Retourne l'émotion ponctuelle à afficher, ou None si cooldown actif.

        Les événements répétés pendant le cooldown fusionnent (badge ×N, §5.4).
        """
        t = time.time() if now is None else now
        if t - self._last_emoji_ts < 2.0:
            if self._pending_merge and self._pending_merge[0] == emotion_id:
                emo, count = self._pending_merge
                self._pending_merge = (emotion_id, count + 1)
            else:
                self._pending_merge = (emotion_id, 1)
            return None
        self._last_emoji_ts = t
        info = PUNCTUAL.get(emotion_id, {"label": emotion_id, "emoji": "•"})
        count = 1
        if self._pending_merge and self._pending_merge[0] == emotion_id:
            count = self._pending_merge[1] + 1
        self._pending_merge = None
        return {"id": emotion_id, **info, "count": count}
