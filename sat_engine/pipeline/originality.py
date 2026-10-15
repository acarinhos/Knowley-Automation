"""5-gram shingle overlap vs reference pages and earlier session questions. Never logs reference text.

Boilerplate (official stems, scaffolds) is ignored: grams inside the stem templates below, and grams found on
>= BOILERPLATE_PAGES distinct (near-duplicate-collapsed) reference pages, EXCEPT when the candidate carries them inside a
verbatim run of >= LONG_RUN_GRAMS consecutive grams (8 words) that also sits on one single reference page. Repeated
formulae inside the session only count accepted questions and never excuse overlap with the reference pages.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

N = 5
RUN_LIMIT = 3  # consecutive copied content shingles (= 7 words) -> reject
JACCARD_LIMIT = 0.2
BOILERPLATE_PAGES = 3
LONG_RUN_GRAMS = 4  # 4 consecutive 5-grams = 8 words
DEDUPE_JACCARD = 0.8
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


def _dedupe(refs: list[set]) -> list[set]:
    """Drop pages that are near copies (Jaccard >= DEDUPE_JACCARD) of an earlier kept page."""
    kept: list[set] = []
    for s in refs:
        if not any(len(s & k) / len(s | k) >= DEDUPE_JACCARD for k in kept if s & k):
            kept.append(s)
    return kept


def template_grams() -> frozenset:
    return frozenset(g for t in TEMPLATES for g in shingles(t))


def frequent_grams(refs: list[set], min_pages: int = BOILERPLATE_PAGES) -> frozenset:
    freq = Counter(g for s in _dedupe(refs) for g in s)
    return frozenset(g for g, c in freq.items() if c >= min_pages)


def build_stoplist(refs: list[set], min_pages: int = BOILERPLATE_PAGES) -> frozenset:
    """Template grams + grams found on >= min_pages distinct pages (check() lifts the latter inside long verbatim runs)."""
    return template_grams() | frequent_grams(refs, min_pages)


class OriginalityChecker:
    def __init__(self, refs: list[set] | None = None, run_limit=RUN_LIMIT, jaccard_limit=JACCARD_LIMIT, stop=None):
        refs = list(reference_sets() if refs is None else refs)
        self.tmpl = template_grams() if stop is None else frozenset(stop)  # always ignored
        self.freq = frequent_grams(refs) - self.tmpl if stop is None else frozenset()  # ignored unless inside a long run
        self.stop = self.tmpl | self.freq
        self.refs = refs
        self._ridx: dict = defaultdict(set)  # gram -> reference page ids
        for i, s in enumerate(refs):
            for g in s:
                self._ridx[g].add(i)
        self.sess: list[set] = []  # accepted session questions
        self._seen: dict = defaultdict(set)  # non-reference gram -> accepted questions containing it
        self._sall: set = set()
        self.run_limit, self.jaccard_limit = run_limit, jaccard_limit

    def _long(self, sh) -> set:
        """Grams of sh lying in a run of LONG_RUN_GRAMS consecutive grams that all sit on one reference page."""
        out, k = set(), LONG_RUN_GRAMS
        for i in range(len(sh) - k + 1):
            ids = set(self._ridx.get(sh[i], ()))
            for g in sh[i + 1:i + k]:
                ids &= self._ridx.get(g, set())
                if not ids:
                    break
            if ids:
                out.update(sh[i:i + k])
        return out

    def _parts(self, q):
        sh = shingles(question_text(q))
        drop = self.tmpl | (self.freq - self._long(sh)) if self.freq else self.tmpl
        mine = {g for g in sh if g not in drop}
        # session comparison also ignores formulae already used by 2 accepted questions; reference comparison never does
        msess = {g for g in mine if len(self._seen.get(g, ())) < BOILERPLATE_PAGES - 1}
        return sh, drop, mine, msess

    def _measure(self, q):
        sh, drop, mine, msess = self._parts(q)
        refall = self._ridx
        jac = max((len(mine & s) / len(mine | (s - drop)) for s in self.refs if mine & s), default=0.0)
        cover = len(mine & refall.keys()) / len(mine) if mine else 0.0
        if msess:
            jac = max(jac, max((len(msess & s) / len(msess | s) for s in self.sess if msess & s), default=0.0))
            cover = max(cover, len(msess & self._sall) / len(msess))
        run = best = 0
        for g in sh:  # a boilerplate gram is in neither mine nor msess, so it ends a run
            run = run + 1 if (g in mine and g in refall) or (g in msess and g in self._sall) else 0
            best = max(best, run)
        return sh, drop, mine, jac, cover, best

    def check(self, q) -> tuple[bool, float]:
        """Returns (ok, score); score in [0,1], 1 = nothing shared with any reference or accepted question."""
        _, _, mine, jac, cover, best = self._measure(q)
        if not mine:
            return True, 1.0
        return (best < self.run_limit and jac <= self.jaccard_limit), round(1 - max(jac, cover), 4)

    def explain(self, q) -> str:
        """Counts only (no text): why check() rejects."""
        sh, drop, mine, jac, cover, best = self._measure(q)
        return (f"grams={len(sh)} boilerplate={sum(g in drop for g in sh)} content={len(mine)} "
                f"shared={round(cover * len(mine))} run={best} jac={jac:.2f}")

    def add(self, q) -> None:
        """Register an ACCEPTED question (only these feed the session comparison and the formula rule)."""
        sh = shingles(question_text(q))
        self.sess.append(set(sh) - self.tmpl)
        self._sall |= self.sess[-1]
        k = hash(question_text(q))
        for g in set(sh):
            if g not in self._ridx:
                self._seen[g].add(k)
