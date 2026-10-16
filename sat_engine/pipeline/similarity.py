"""Scenario/topic similarity between questions: stopword-free TF-IDF cosine over passage + stem + options (template stems
ignored) plus shared rare named entities / numbers. No LLM, no reference text."""
from __future__ import annotations

import math
import re
from collections import Counter

STOP = frozenset("""a about above after again against all also am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his how i if in into is it its
just me more most my no nor not now of off on once only or other our out over own same she should so some such than that the their them
then there these they this those through to too under until up very was we were what when where which while who whom why will with would
you your yes one two three four five six seven eight nine ten per
equation expression function graph value system solution line point number following equivalent real constant coefficient integer
variable triangle angle right circle area length volume radius rate percent total find equal given determine statement
true correct best likely text passage author claim""".split())  # last 2 rows: generic math/exam vocabulary, not scenario content
_TEMPLATES = (
    r"Which choice[^?]*\?", r"While researching a topic, a student has taken the following notes:?",
    r"The student wants to [^.?]*\.", r"Based on the texts?,[^?]*\?", r"According to the text,?[^?]*\?",
    r"Which finding, if true,[^?]*\?", r"Text [12]:", r"What is the (value|measure|length|area|volume)[^?]*\?",
    r"Which of the following[^?]*\?", r"\bNotes?:", r"in the xy-plane", r"the graph shown", r"the figure shown",
)
_RE_TEMPLATES = re.compile("|".join(_TEMPLATES), re.I)
THRESHOLD = 0.17      # tf-idf cosine for a "same scenario" pair (calibrated on seed101-rw-math, see dedupe.format_report) ...
MIN_SHARED = 5        # ... AND at least this many shared informative terms (kills 1-2 word coincidences in short math stems)
ENTITY_THRESHOLD = 0.10  # lower bar when the pair also shares >= ENTITY_MIN rare named entities / numbers
ENTITY_MIN = 2
RARE_DF = 3           # an entity/number is "rare" when it occurs in at most this many items of the set
COMMON_DF = 0.10      # terms in more than 10% of the items (min 5) are generic and ignored


def content_text(q) -> str:
    return " ".join(filter(None, [q.passage, _RE_TEMPLATES.sub(" ", q.stem or ""), *(q.options or {}).values()]))


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z][a-z'-]*|\d+(?:\.\d+)?", text.lower()):
        w = w.strip("'-")
        if w in STOP or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]  # shortcut: crude plural fold instead of a real stemmer
        out.append(w)
    return out


def entities(text: str) -> set[str]:
    """Capitalised words not opening a sentence, and numbers with >= 2 digits / decimals."""
    ents = {m[1].lower() for m in re.finditer(r"(?<=[a-z,;:] )([A-Z][a-zA-Z-]{2,})", text)}
    ents |= {n for n in re.findall(r"\d+(?:\.\d+)?", text) if len(n) >= 2}
    return ents - STOP


class Index:
    """tf-idf vectors + entity sets for a set of questions (key -> SATQuestion)."""

    def __init__(self, qs: dict):
        docs = {k: Counter(tokens(content_text(q))) for k, q in qs.items()}
        n = len(docs)
        df = Counter(w for d in docs.values() for w in d)
        cut = max(5, COMMON_DF * n)
        self.vec, self.terms = {}, {}
        for k, d in docs.items():
            v = {w: (1 + math.log(c)) * (math.log((1 + n) / (1 + df[w])) + 1) for w, c in d.items() if df[w] <= cut}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            self.vec[k] = {w: x / norm for w, x in v.items()}
            self.terms[k] = set(v)
        self.ents = {k: entities(content_text(q)) for k, q in qs.items()}
        edf = Counter(e for s in self.ents.values() for e in s)
        self.rare = {k: {e for e in s if edf[e] <= RARE_DF} for k, s in self.ents.items()}

    def score(self, a, b) -> tuple[float, int, list]:
        va, vb = self.vec[a], self.vec[b]
        if len(va) > len(vb):
            va, vb = vb, va
        return (round(sum(x * vb[w] for w, x in va.items() if w in vb), 4), len(self.terms[a] & self.terms[b]),
                sorted(self.rare[a] & self.rare[b]))


def is_similar(cos: float, shared: int, ents: list, threshold: float = THRESHOLD) -> bool:
    return (cos >= threshold and shared >= MIN_SHARED) or (cos >= ENTITY_THRESHOLD and len(ents) >= ENTITY_MIN)


def pair_scores(qs: dict) -> list[tuple]:
    """[(cosine, a, b, shared_terms, shared_rare_entities)] for every pair (a sorts before b), highest first."""
    keys = sorted(qs, key=lambda k: (0, int(k)) if str(k).isdigit() else (1, str(k)))
    ix = Index(qs)
    out = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            c, n, e = ix.score(a, b)
            out.append((c, a, b, n, e))
    return sorted(out, key=lambda x: -x[0])


def similar_pairs(qs: dict, threshold: float = THRESHOLD) -> list[tuple]:
    return [p for p in pair_scores(qs) if is_similar(p[0], p[3], p[4], threshold)]


def clusters(pairs: list[tuple]) -> list[list]:
    """Connected components of the similar-pair graph, each sorted by numeric key."""
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for p in pairs:
        parent[find(p[1])] = find(p[2])
    groups: dict = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    return sorted((sorted(g, key=int) for g in groups.values()), key=lambda g: int(g[0]))


def check_candidate(cand, qs: dict, ignore=(), threshold: float = THRESHOLD):
    """Compare a candidate with every item of qs (except keys in `ignore`): (similar?, best cosine, other key, shared entities)."""
    pool = {k: q for k, q in qs.items() if k not in ignore}
    ix = Index({**pool, "_cand": cand})  # idf over pool + candidate, like the pairwise scan
    best = (0.0, None, [])
    for k in pool:
        c, n, e = ix.score("_cand", k)
        if is_similar(c, n, e, threshold):
            return True, c, k, e
        best = max(best, (c, k, e), key=lambda x: x[0])
    return False, best[0], best[1], best[2]
