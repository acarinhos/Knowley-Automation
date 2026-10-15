"""Verbatim guard: reject blueprint text sharing a >=N-word run with its source page."""
import re

N = 5


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _grams(words, n=N):
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def blueprint_texts(bp) -> list[str]:
    return [bp.stem_pattern, bp.passage_genre or "", *bp.reasoning_steps, *bp.distractor_types]


def copies_source(texts: list[str], page_text: str, n: int = N) -> bool:
    src = _grams(_words(page_text), n)
    return any(_grams(_words(t), n) & src for t in texts)
