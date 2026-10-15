"""5-gram shingle overlap vs reference pages and earlier session questions. Never logs reference text."""
from __future__ import annotations

import re

N = 5
RUN_LIMIT = 3  # consecutive copied shingles (= 7 words) -> reject
JACCARD_LIMIT = 0.2
_cache: list[set] | None = None
_BOILERPLATE = re.compile(r"Which choice[^?]*\?", re.I)


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


class OriginalityChecker:
    def __init__(self, refs: list[set] | None = None, run_limit=RUN_LIMIT, jaccard_limit=JACCARD_LIMIT):
        self.sets = list(reference_sets() if refs is None else refs)  # copy: session questions are appended
        self.run_limit, self.jaccard_limit = run_limit, jaccard_limit

    def check(self, q) -> tuple[bool, float]:
        """Returns (ok, score); score in [0,1], 1 = nothing shared with any reference."""
        sh = shingles(question_text(q))
        mine = set(sh)
        if not mine:
            return True, 1.0
        jac = max((len(mine & s) / len(mine | s) for s in self.sets if mine & s), default=0.0)
        allref = set().union(*self.sets) if self.sets else set()
        run = best = 0
        for g in sh:
            run = run + 1 if g in allref else 0
            best = max(best, run)
        cover = len(mine & allref) / len(mine)
        return (best < self.run_limit and jac <= self.jaccard_limit), round(1 - max(jac, cover), 4)

    def add(self, q) -> None:
        self.sets.append(set(shingles(question_text(q))))
