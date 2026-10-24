"""Per (category, subcategory) memory of topics/entities already used, to keep the LLM from repeating them."""
import threading
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def topic_terms(q: Any) -> List[str]:
    """keyword + correct-answer option text from the en (else tr, else first) translation; works on models and dicts."""
    translations = _get(q, "translations") or {}
    tr = next(
        (translations[k] for k in ("en", "tr") if translations.get(k)),
        next(iter(translations.values()), None),
    )
    if tr is None:
        return []
    options = _get(tr, "options") or {}
    letter = _get(q, "correct_answer") or _get(q, "correct_option")
    answer = options.get(letter) if isinstance(options, dict) else _get(options, str(letter or ""))
    return [str(t).strip() for t in (_get(tr, "keyword"), answer) if t and str(t).strip()]


class TopicDiversityTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Dict[Tuple[str, str], Counter] = {}
        self._display: Dict[Tuple[str, str, str], str] = {}
        self._seeded: set = set()

    @staticmethod
    def _key(cat: str, sub: str) -> Tuple[str, str]:
        return (str(cat or "").strip().casefold(), str(sub or "").strip().casefold())

    def record(self, cat: str, sub: str, terms: Iterable[str]) -> None:
        key = self._key(cat, sub)
        with self._lock:
            for term in terms:
                norm = " ".join(str(term).split()).casefold()
                if norm:
                    self._counts.setdefault(key, Counter())[norm] += 1
                    self._display.setdefault(key + (norm,), " ".join(str(term).split()))

    def recent(self, cat: str, sub: str, limit: int = 40) -> List[str]:
        key = self._key(cat, sub)
        with self._lock:
            ranked = self._counts.get(key, Counter()).most_common(limit)  # ties keep insertion order
            return [self._display[key + (norm,)] for norm, _ in ranked]

    def seed_from_db(self, db: Any, cat: str, sub: str, limit: int = 50) -> None:
        """Lazy, once per (cat, sub). Any DB problem is swallowed: generation must never block on this."""
        key = self._key(cat, sub)
        with self._lock:
            if key in self._seeded:
                return
            self._seeded.add(key)
        try:
            # shortcut: unordered (no composite index needed), so "last N" is any N; upgrade with order_by created_at + index.
            docs = db.collection("questions").where("sub_categories", "array_contains", sub).limit(limit).stream()
            for doc in docs:
                data = doc.to_dict() or {}
                if cat in (data.get("categories") or [cat]):
                    self.record(cat, sub, topic_terms(data))
        except Exception:
            pass


def avoid_line(tracker: TopicDiversityTracker, cat: str, sub: str) -> str:
    topics = tracker.recent(cat, sub)
    if not topics:
        return ""
    return (
        f"\nBu alt kategoride daha önce işlenen konular/varlıklar: {', '.join(topics)}. "
        "Bunları TEKRAR ETME; alt kategorinin geniş kapsamı içinden farklı, daha az bilinen ama genel kültüre uygun "
        "bir konu/varlık seç. Aynı varlığı sorunun cevabı veya ana öznesi yapma."
    )
