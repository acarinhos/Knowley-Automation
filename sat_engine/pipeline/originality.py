"""5-gram shingle overlap vs reference pages and earlier session questions. Never logs reference text.

Boilerplate (official stems, scaffolds, answer formulae) is auto-detected and ignored: word 5-grams that occur on
>= BOILERPLATE_PAGES distinct reference pages or inside the stem templates below. Overlap and Jaccard are computed on
content shingles only.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

N = 5
RUN_LIMIT = 3  # consecutive copied content shingles (= 7 words) -> reject
JACCARD_LIMIT = 0.2
BOILERPLATE_PAGES = 3
_cache: list[set] | None = None
_BOILERPLATE = re.compile(r"Which choice[^?]*\?", re.I)
TEMPLATES = (
    "Which choice most effectively uses relevant information from the notes to accomplish this goal",
    "While researching a topic, a student has taken the following notes",
    "The student wants to emphasize, present, compare, explain, introduce, illustrate, support, make a generalization",
    "Based on the texts, how would the author of Text 2 most likely respond to the claim in Text 1",
    "Text 1 Text 2 Based on the texts both authors the author of Text 1 would most likely",
    "Which choice completes the text so that it conforms to the conventions of Standard English",
    "Which choice completes the text with the most logical transition",
    "Which choice completes the text with the most logical and precise word or phrase",
    "Which choice most logically completes the text",
    "Which choice best states the main purpose of the text",
    "Which choice best describes the overall structure of the text",
    "Which choice best states the main idea of the text",
    "Which finding, if true, would most directly support the claim",
    "Which finding, if true, would most directly weaken the claim",
    "Which choice most effectively uses data from the table to complete the statement",
    "According to the text, what is true about",
    "The following text is adapted from the work of",
)


def shingles(text: str) -> list[tuple]:
    w = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [tuple(w[i:i + N]) for i in range(len(w) - N + 1)]


def question_text(q) -> str:
    stem = _BOILERPLATE.sub("", q.stem or "")  # official stems ("Which choice ...?") are formulaic, not content
    return " ".join(filter(None, [q.passage, stem, *(q.options or {}).values()]))


def reference_sets() -> list[set]:
    global _cache
    if _cache is None:
        from ..ingest import extract
        _cache = []
        if extract.REF_DIR.is_dir():
            for src in sorted(p.name for p in extract.REF_DIR.iterdir() if p.is_dir()):
                _cache += [set(shingles(t)) for _, t in extract.iter_pages(src)]
    return _cache


def build_stoplist(refs: list[set], min_pages: int = BOILERPLATE_PAGES) -> frozenset:
    """Template grams + grams found on >= min_pages distinct pages."""
    freq = Counter(g for s in refs for g in s)
    stop = {g for t in TEMPLATES for g in shingles(t)} | {g for g, c in freq.items() if c >= min_pages}
    return frozenset(stop)


class OriginalityChecker:
    def __init__(self, refs: list[set] | None = None, run_limit=RUN_LIMIT, jaccard_limit=JACCARD_LIMIT, stop=None):
        refs = list(reference_sets() if refs is None else refs)  # copy: session questions are appended
        self.stop = build_stoplist(refs) if stop is None else frozenset(stop)
        self.sets = [s - self.stop for s in refs]  # content shingles only
        self._ref = set().union(*refs) if refs else set()
        self._seen: dict = defaultdict(set)  # non-reference gram -> distinct candidates (checked or accepted) containing it
        self.run_limit, self.jaccard_limit = run_limit, jaccard_limit

    def _is_stop(self, g) -> bool:
        return g in self.stop or len(self._seen.get(g, ())) >= BOILERPLATE_PAGES - 1  # formula already used by 2 other candidates

    def _note(self, q, mine) -> None:
        k = hash(question_text(q))
        for g in mine:
            if g not in self._ref:
                self._seen[g].add(k)

    def _grams(self, q):
        sh = shingles(question_text(q))
        return sh, {g for g in sh if not self._is_stop(g)}

    def check(self, q) -> tuple[bool, float]:
        """Returns (ok, score); score in [0,1], 1 = nothing shared with any reference."""
        sh, mine = self._grams(q)
        if not mine:
            return True, 1.0
        jac = max((len(mine & s) / len(mine | s) for s in self.sets if mine & s), default=0.0)
        allref = set().union(*self.sets) if self.sets else set()
        run = best = 0
        for g in sh:  # a boilerplate gram is never in allref, so it also ends a run
            run = run + 1 if g in allref and g in mine else 0
            best = max(best, run)
        cover = len(mine & allref) / len(mine)
        self._note(q, mine)  # rejected candidates count too: a phrase tried 3 times is a formula, not copying
        return (best < self.run_limit and jac <= self.jaccard_limit), round(1 - max(jac, cover), 4)

    def explain(self, q) -> str:
        """Counts only (no text): why check() rejects."""
        sh, mine = self._grams(q)
        allref = set().union(*self.sets) if self.sets else set()
        run = best = 0
        for g in sh:
            run = run + 1 if g in allref and g in mine else 0
            best = max(best, run)
        jac = max((len(mine & s) / len(mine | s) for s in self.sets if mine & s), default=0.0)
        return (f"grams={len(sh)} boilerplate={sum(self._is_stop(g) for g in sh)} content={len(mine)} "
                f"shared={len(mine & allref)} run={best} jac={jac:.2f}")

    def add(self, q) -> None:
        g = self._grams(q)[1]
        self.sets.append(g)
        self._note(q, g)
