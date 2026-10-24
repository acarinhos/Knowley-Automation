"""Per (category, subcategory) memory of topics/entities already used, to keep the LLM from repeating them."""
import logging
import threading
from collections import OrderedDict
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


MAX_TERM_LEN = 40
MAX_TERMS_PER_KEY = 200  # memory cap per (cat, sub); oldest evicted first
SEED_TIMEOUT_S = 10.0


class TopicDiversityTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seed_lock = threading.Lock()  # separate: a slow DB seed must not block record()/recent()
        self._terms: Dict[Tuple[str, str], "OrderedDict[str, list]"] = {}  # norm -> [count, display]; order = recency
        self._seeded: set = set()

    @staticmethod
    def _key(cat: str, sub: str) -> Tuple[str, str]:
        return (str(cat or "").strip().casefold(), str(sub or "").strip().casefold())

    def record(self, cat: str, sub: str, terms: Iterable[str]) -> None:
        key = self._key(cat, sub)
        with self._lock:
            for term in terms:
                display = " ".join(str(term).split())
                if len(display) < 3 or len(display) > MAX_TERM_LEN or display.replace(".", "").replace(",", "").isdigit():
                    continue
                norm = display.casefold()
                bucket = self._terms.setdefault(key, OrderedDict())
                entry = bucket.setdefault(norm, [0, display])
                entry[0] += 1
                bucket.move_to_end(norm)
                while len(bucket) > MAX_TERMS_PER_KEY:
                    bucket.popitem(last=False)

    def recent(self, cat: str, sub: str, limit: int = 40) -> List[str]:
        with self._lock:
            bucket = self._terms.get(self._key(cat, sub), {})
            newest_first = list(reversed(bucket.values()))
        newest_first.sort(key=lambda e: -e[0])  # stable: ties stay newest-first
        return [display for _, display in newest_first[:limit]]

    def seed_from_db(self, db: Any, cat: str, sub: str, limit: int = 50) -> None:
        """Lazy, once per (cat, sub) after a successful query; failures are logged and retried next call. Never raises."""
        key = self._key(cat, sub)
        with self._seed_lock:
            if key in self._seeded:
                return
            try:
                # shortcut: unordered (no composite index needed), so "last N" is any N; upgrade with order_by created_at + index.
                docs = db.collection("questions").where("sub_categories", "array_contains", sub).limit(limit).stream(timeout=SEED_TIMEOUT_S)
                for doc in docs:
                    data = doc.to_dict() or {}
                    if cat in (data.get("categories") or [cat]):
                        self.record(cat, sub, topic_terms(data))
                self._seeded.add(key)
            except Exception as exc:
                logging.warning("topic diversity seed failed for %s/%s: %s", cat, sub, exc)


def avoid_line(tracker: TopicDiversityTracker, cat: str, sub: str) -> str:
    topics = tracker.recent(cat, sub)
    if not topics:
        return ""
    return (
        f"\nBu alt kategoride daha önce işlenen konular/varlıklar: {', '.join(topics)}. "
        "Bunları TEKRAR ETME; alt kategorinin geniş kapsamı içinden farklı, daha az bilinen ama genel kültüre uygun "
        "bir konu/varlık seç. Aynı varlığı sorunun cevabı veya ana öznesi yapma."
    )
