"""Normal zorluktan bağımsız, 10'lu blok başlangıç zorluğu etiketleyicisi."""

from __future__ import annotations

import random
from typing import Any, Optional


class StartingDifficultyAssigner:
    """Her 10 normal üretimde 3 Kolay + 1 Orta soruyu bağımsız etiketler.

    Normal DifficultyBalancer 10'lu bloklarda 4 Kolay / 4 Orta / 2 Zor üretir.
    Bu sınıf o sonuçları değiştirmez; yalnızca uygun dört soruya ek alan verir.
    """

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self.rng = rng or random.Random()
        self._position = 0
        self._easy_seen = 0
        self._medium_seen = 0
        self._easy_labels_used = 0
        self._reset_choices()

    def _reset_choices(self) -> None:
        self._skipped_easy_ordinal = self.rng.randint(1, 4)
        self._selected_medium_ordinal = self.rng.randint(1, 4)

    def assign(self, question: Any) -> Optional[str]:
        """Soruyu değiştirmeden yalnızca seçildiyse `starting_difficulty` ekler."""
        level = str(
            question.get("difficulty_local", "")
            if isinstance(question, dict)
            else getattr(question, "difficulty_local", "")
        ).strip().capitalize()

        self._position += 1
        starting_difficulty: Optional[str] = None
        if level == "Kolay":
            self._easy_seen += 1
            if self._easy_seen <= 4 and self._easy_seen != self._skipped_easy_ordinal:
                labels = ("Kolay", "Orta", "Zor")
                if self._easy_labels_used < len(labels):
                    starting_difficulty = labels[self._easy_labels_used]
                    self._easy_labels_used += 1
        elif level == "Orta":
            self._medium_seen += 1
            if self._medium_seen == self._selected_medium_ordinal:
                starting_difficulty = "Zor"

        if starting_difficulty:
            if isinstance(question, dict):
                question["starting_difficulty"] = starting_difficulty
            else:
                setattr(question, "starting_difficulty", starting_difficulty)

        if self._position == 10:
            self._position = 0
            self._easy_seen = 0
            self._medium_seen = 0
            self._easy_labels_used = 0
            self._reset_choices()
        return starting_difficulty
