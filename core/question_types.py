"""Question-type variable for normal generation (SAT is untouched).

multiple_choice is the default and the only type outside the dedicated "question_types" mode.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, List, Optional, Tuple

MULTIPLE_CHOICE = "multiple_choice"
TRUE_FALSE = "true_false"
FILL_BLANK = "fill_blank"
QUESTION_TYPES = (MULTIPLE_CHOICE, TRUE_FALSE, FILL_BLANK)
NEW_QUESTION_TYPES = (TRUE_FALSE, FILL_BLANK)
QUESTION_TYPES_MODE = "question_types"
BLANK_MARKER = "_____"


def normalize_question_types(value: Any) -> List[str]:
    """Accept list / comma string / 'mixed'; return a de-duplicated list of the NEW types (default: both)."""
    if value is None or value == "" or value == []:
        return list(NEW_QUESTION_TYPES)
    items: Iterable[Any] = value.split(",") if isinstance(value, str) else value
    result: List[str] = []
    for item in items:
        key = str(item).strip().lower()
        if key in ("mixed", "karisik", "both"):
            result.extend(NEW_QUESTION_TYPES)
        elif key in NEW_QUESTION_TYPES:
            result.append(key)
        else:
            raise ValueError("Soru tipi true_false, fill_blank veya karışık olmalıdır.")
    return list(dict.fromkeys(result)) or list(NEW_QUESTION_TYPES)


class QuestionTypePlanner:
    """Round-robin over the chosen types; true_false slots alternate the target truth value (balanced ~50/50)."""

    def __init__(self, types: Optional[List[str]] = None) -> None:
        self.types = list(types or [MULTIPLE_CHOICE])
        self._index = 0
        self._tf_index = 0
        self._lock = threading.Lock()

    def next(self) -> Tuple[str, Optional[bool]]:
        """(question_type, true_false_answer). The second item is None unless the type is true_false."""
        with self._lock:
            question_type = self.types[self._index % len(self.types)]
            self._index += 1
            if question_type != TRUE_FALSE:
                return question_type, None
            truth = self._tf_index % 2 == 0
            self._tf_index += 1
            return question_type, truth
