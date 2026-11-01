"""Admin paneli için Firestore soru listeleme / düzenleme / silme (yalnızca sunucu tarafı).

Yazma işlemleri KNOWLEY_QUESTION_EDITS_ENABLED=true olmadan reddedilir.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from core.question_types import ALL_QUESTION_COLLECTIONS
from core.translation_service import SUPPORTED_LANGUAGE_CODES

# Bump whenever an /api/admin/questions endpoint is added; the local proxy refuses stale services.
API_REVISION = 3
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
# shortcut: filters other than the date range run in Python on a bounded scan (no composite indexes); raise the cap or add indexes if filtered pages come back sparse.
MAX_SCAN_PER_PAGE = 600
SCAN_BATCH = 200
# tf/fs documents duplicate the statement in binary_fallback/payload and tie correct_answer to it; only list/delete are safe there.
EDITABLE_COLLECTIONS = ("questions",)
DIFFICULTIES = ("Kolay", "Orta", "Zor")
EDITABLE_FIELDS = {"language", "question", "options", "correct_answer", "category", "sub_category"}
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_QUESTION_CHARS = 1000
MAX_OPTION_CHARS = 500


class EditsDisabledError(PermissionError):
    """Soru düzenleme/silme yerel ayarlarda kapalı."""


def question_edits_enabled() -> bool:
    return os.getenv("KNOWLEY_QUESTION_EDITS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def require_edits_enabled() -> None:
    if not question_edits_enabled():
        raise EditsDisabledError("Soru düzenleme/silme kapalı (KNOWLEY_QUESTION_EDITS_ENABLED).")


def _collection(name: Any) -> str:
    value = str(name or "questions").strip()
    if value not in ALL_QUESTION_COLLECTIONS:
        raise ValueError("Geçersiz soru koleksiyonu.")
    return value


def _doc_id(value: Any) -> str:
    value = str(value or "")
    if not DOC_ID_RE.match(value):
        raise ValueError("Geçersiz soru kimliği.")
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first(values: Any) -> str:
    if isinstance(values, str):
        return values.strip()
    if isinstance(values, (list, tuple)):
        for item in values:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return value if isinstance(value, str) else ""


def _parse_day(value: Any, end_of_day: bool = False) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.") from exc
    return day + timedelta(days=1) if end_of_day else day  # upper bound is exclusive


def _language_field(language: str) -> str:
    if language not in SUPPORTED_LANGUAGE_CODES:
        raise ValueError("Desteklenmeyen dil kodu.")
    return language


def question_row(doc_id: str, data: Mapping[str, Any], language: str = "") -> Dict[str, Any]:
    translations = data.get("translations") if isinstance(data.get("translations"), Mapping) else {}

    def text_in(code: str) -> str:
        item = translations.get(code)
        return _text(item.get("question")) if isinstance(item, Mapping) else ""

    shown = language if language and text_in(language) else next(
        (code for code in (language, "tr", "en") if code and text_in(code)),
        next((code for code in translations if text_in(code)), ""),
    )
    difficulty = _text(data.get("difficulty")) or _text(data.get("difficulty_local"))
    return {
        "id": doc_id,
        "question": text_in(shown) if shown else _text(data.get("question")),
        "language": shown,
        "languages": [code for code in translations if text_in(code)] or list(data.get("supported_languages") or []),
        "category": _first(data.get("categories") or data.get("category")),
        "sub_category": _first(data.get("sub_categories") or data.get("subcategory")),
        "country": _text(data.get("target_country")) or _first(data.get("countries")),
        "country_code": _text(data.get("target_country_code")),
        "scope": _text(data.get("scope")),
        "difficulty": difficulty,
        "version": _text(data.get("dbVersion")) or _text(data.get("version")),
        "created_at": _iso(data.get("created_at")),
    }


def _matches(row: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
    if filters["category"] and row["category"] != filters["category"]:
        return False
    if filters["language"] and filters["language"] not in row["languages"]:
        return False
    if filters["country"] and filters["country"].casefold() not in (
        row["country_code"].casefold(), row["country"].casefold()
    ):
        return False
    if filters["difficulty"] and row["difficulty"] != filters["difficulty"]:
        return False
    if filters["q"] and filters["q"].casefold() not in row["question"].casefold():
        return False
    return True


def list_questions(db: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
    """Sayfalı liste. params: collection, limit, cursor (son belge kimliği), category, language, country,
    difficulty, q, created_from, created_to (YYYY-MM-DD)."""
    from google.cloud.firestore_v1.base_query import FieldFilter

    collection = _collection(params.get("collection"))
    limit = max(1, min(MAX_PAGE_SIZE, int(params.get("limit") or DEFAULT_PAGE_SIZE)))
    filters = {key: _text(params.get(key)) for key in ("category", "language", "country", "difficulty", "q")}
    if filters["language"]:
        _language_field(filters["language"])
    if filters["difficulty"] and filters["difficulty"] not in DIFFICULTIES:
        raise ValueError("Geçersiz zorluk filtresi.")
    start, end = _parse_day(params.get("created_from")), _parse_day(params.get("created_to"), end_of_day=True)

    query = db.collection(collection).order_by("created_at", direction="DESCENDING")
    if start:
        query = query.where(filter=FieldFilter("created_at", ">=", start))
    if end:
        query = query.where(filter=FieldFilter("created_at", "<", end))
    paths = ["translations.tr.question", "translations.en.question", "supported_languages", "categories",
             "category", "sub_categories", "target_country", "target_country_code", "countries", "scope",
             "difficulty", "difficulty_local", "dbVersion", "version", "created_at", "question"]
    if filters["language"]:
        paths.append(f"translations.{filters['language']}.question")
    query = query.select(paths)

    cursor = _text(params.get("cursor"))
    if cursor:
        snapshot = db.collection(collection).document(_doc_id(cursor)).get()
        if snapshot.exists:
            query = query.start_after(snapshot)

    rows: List[Dict[str, Any]] = []
    scanned, last_id, exhausted = 0, "", False
    while len(rows) < limit and scanned < MAX_SCAN_PER_PAGE:
        size = min(SCAN_BATCH, MAX_SCAN_PER_PAGE - scanned)
        batch = query.limit(size).get()
        if not batch:
            exhausted = True
            break
        for index, snapshot in enumerate(batch):
            scanned += 1
            last_id = snapshot.id
            row = question_row(snapshot.id, snapshot.to_dict() or {}, filters["language"])
            if _matches(row, filters):
                rows.append(row)
                if len(rows) >= limit:
                    batch = batch[: index + 1]
                    break
        if len(batch) < size and len(rows) < limit:
            exhausted = True
            break
        query = query.start_after(batch[-1])
    return {
        "collection": collection,
        "items": rows,
        "next_cursor": "" if exhausted else last_id,
        "scanned": scanned,
        "limit": limit,
    }


def get_question(db: Any, doc_id: str, collection: Any = None) -> Dict[str, Any]:
    """Düzenleme için tek soru (yalnızca izin verilen alanlar)."""
    collection = _collection(collection)
    snapshot = db.collection(collection).document(_doc_id(doc_id)).get()
    if not snapshot.exists:
        raise KeyError(doc_id)
    data = snapshot.to_dict() or {}
    translations = data.get("translations") if isinstance(data.get("translations"), Mapping) else {}
    return {
        "id": snapshot.id,
        "collection": collection,
        **question_row(snapshot.id, data),
        "correct_answer": _text(data.get("correct_answer")) or _text(data.get("correct_option")),
        "translations": {
            code: {"question": _text(item.get("question")), "options": dict(item.get("options") or {})}
            for code, item in translations.items()
            if isinstance(item, Mapping) and code in SUPPORTED_LANGUAGE_CODES
        },
    }


def _validate_update(
    payload: Mapping[str, Any],
    data: Mapping[str, Any],
    categories: Sequence[str],
    subcategories_for: Callable[[str], Sequence[str]],
) -> Dict[str, Any]:
    unknown = set(payload) - EDITABLE_FIELDS - {"collection"}
    if unknown:
        raise ValueError(f"Düzenlenemeyen alan(lar): {', '.join(sorted(unknown))}")
    if not any(key in payload for key in EDITABLE_FIELDS):
        raise ValueError("Güncellenecek alan yok.")
    updates: Dict[str, Any] = {}
    translations = data.get("translations") if isinstance(data.get("translations"), Mapping) else {}

    if {"question", "options"} & set(payload):
        language = _language_field(_text(payload.get("language")))
        current = translations.get(language)
        if not isinstance(current, Mapping):
            raise ValueError("Bu soruda seçilen dil çevirisi yok.")
        if "question" in payload:
            question = _text(payload["question"])
            if not question or len(question) > MAX_QUESTION_CHARS:
                raise ValueError(f"Soru metni 1-{MAX_QUESTION_CHARS} karakter olmalıdır.")
            updates[f"translations.{language}.question"] = question
        if "options" in payload:
            options, existing = payload["options"], current.get("options")
            if not isinstance(options, Mapping) or not isinstance(existing, Mapping) or set(options) != set(existing):
                raise ValueError("Seçenekler mevcut seçenek anahtarlarıyla birebir aynı olmalıdır.")
            clean = {key: _text(value) for key, value in options.items()}
            if any(not value or len(value) > MAX_OPTION_CHARS for value in clean.values()):
                raise ValueError(f"Her seçenek 1-{MAX_OPTION_CHARS} karakter olmalıdır.")
            updates[f"translations.{language}.options"] = clean

    if "correct_answer" in payload:
        answer = _text(payload["correct_answer"]).upper()
        keys = {key for item in translations.values() if isinstance(item, Mapping) for key in (item.get("options") or {})}
        if answer not in (keys or {"A", "B", "C", "D"}):
            raise ValueError("Doğru cevap mevcut seçeneklerden biri olmalıdır.")
        updates["correct_answer"] = updates["correct_option"] = answer

    if "category" in payload:
        category = _text(payload["category"])
        if category not in categories:
            raise ValueError("Geçersiz kategori.")
        from config.categories_config import get_category_color, get_subcategory_image

        current = data.get("categories") or data.get("category") or []
        if isinstance(current, str):
            current = [current]
        if len(current) > 1:
            raise ValueError("Birden fazla kategorisi olan sorunun kategorisi buradan değiştirilemez.")
        updates["categories"] = [category]
        if "category" in data and data["category"] is not None:
            updates["category"] = [category] if isinstance(data["category"], list) else category
        updates["categories_meta"] = [{"name": category, "color": get_category_color(category)}]
        sub = _text(payload.get("sub_category"))
        if sub and sub not in subcategories_for(category):
            raise ValueError("Alt kategori seçilen kategoriye ait değil.")
        if sub:
            updates["sub_categories"] = [sub]
            updates["sub_categories_meta"] = [{"name": sub, "image_url": get_subcategory_image(category, sub)}]
    elif "sub_category" in payload:
        raise ValueError("Alt kategori yalnızca kategoriyle birlikte değiştirilebilir.")
    return updates


def update_question(
    db: Any,
    doc_id: str,
    payload: Mapping[str, Any],
    categories: Sequence[str],
    subcategories_for: Callable[[str], Sequence[str]],
    collection: Any = None,
) -> Dict[str, Any]:
    require_edits_enabled()
    collection = _collection(collection or payload.get("collection"))
    if collection not in EDITABLE_COLLECTIONS:
        raise ValueError("Bu koleksiyonda düzenleme desteklenmiyor; yalnızca listeleme ve silme yapılabilir.")
    ref = db.collection(collection).document(_doc_id(doc_id))
    snapshot = ref.get()
    if not snapshot.exists:
        raise KeyError(doc_id)
    updates = _validate_update(payload, snapshot.to_dict() or {}, categories, subcategories_for)
    from firebase_admin import firestore

    ref.update({**updates, "updated_at": firestore.SERVER_TIMESTAMP})
    invalidate_count_cache()
    return {"id": doc_id, "collection": collection, "updated_fields": sorted(updates)}


def delete_question(db: Any, doc_id: str, collection: Any = None) -> Dict[str, Any]:
    require_edits_enabled()
    collection = _collection(collection)
    ref = db.collection(collection).document(_doc_id(doc_id))
    if not ref.get().exists:
        raise KeyError(doc_id)
    ref.delete()
    invalidate_count_cache()
    return {"id": doc_id, "collection": collection, "deleted": True}


# shortcut: Python-side filters need a full field-masked scan; capped, so beyond this the total is a lower bound ("≥N"). Add indexes + count() aggregation to make it exact.
COUNT_SCAN_CAP = 20000
COUNT_CACHE_TTL = 60.0
_COUNT_CACHE: Dict[Any, Any] = {}
_COUNT_LOCK = threading.Lock()


def invalidate_count_cache() -> None:
    with _COUNT_LOCK:
        _COUNT_CACHE.clear()


def count_questions(db: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
    """Filtreye uyan toplam, listeyle aynı küme (created_at olmayan belgeler ikisinde de yok).
    Yalnızca koleksiyon/tarih filtresi varsa Firestore count(); aksi halde sınırlı tarama. 60 sn önbellekli."""
    from google.cloud.firestore_v1.base_query import FieldFilter

    collection = _collection(params.get("collection"))
    filters = {key: _text(params.get(key)) for key in ("category", "language", "country", "difficulty", "q")}
    if filters["language"]:
        _language_field(filters["language"])
    if filters["difficulty"] and filters["difficulty"] not in DIFFICULTIES:
        raise ValueError("Geçersiz zorluk filtresi.")
    start, end = _parse_day(params.get("created_from")), _parse_day(params.get("created_to"), end_of_day=True)

    key = (collection, tuple(sorted(filters.items())), start, end)
    now = time.monotonic()
    with _COUNT_LOCK:
        hit = _COUNT_CACHE.get(key)
    if hit and now - hit[0] < COUNT_CACHE_TTL:
        return {**hit[1], "cached": True}

    # order_by("created_at") is what the list uses; it also drops documents without that field.
    query = db.collection(collection).order_by("created_at", direction="DESCENDING")
    if start:
        query = query.where(filter=FieldFilter("created_at", ">=", start))
    if end:
        query = query.where(filter=FieldFilter("created_at", "<", end))

    if not any(filters.values()):
        result = {"collection": collection, "count": int(query.count().get()[0][0].value), "exact": True, "method": "aggregation"}
    else:
        # created_at stays in the mask so start_after can use the snapshot as a cursor.
        paths = ["translations.tr.question", "translations.en.question", "supported_languages", "categories",
                 "category", "target_country", "target_country_code", "countries", "difficulty", "difficulty_local",
                 "question", "created_at"]
        if filters["language"]:
            paths.append(f"translations.{filters['language']}.question")
        query = query.select(paths)
        total, scanned, truncated = 0, 0, False
        while not truncated:
            size = min(SCAN_BATCH * 5, COUNT_SCAN_CAP + 1 - scanned)  # one extra read decides exactness
            batch = query.limit(size).get()
            for snapshot in batch:
                if scanned >= COUNT_SCAN_CAP:
                    truncated = True
                    break
                scanned += 1
                if _matches(question_row(snapshot.id, snapshot.to_dict() or {}, filters["language"]), filters):
                    total += 1
            if truncated or len(batch) < size:
                break
            query = query.start_after(batch[-1])
        result = {"collection": collection, "count": total, "exact": not truncated, "method": "scan", "scanned": scanned}
    with _COUNT_LOCK:
        _COUNT_CACHE[key] = (now, result)
    return {**result, "cached": False}


def _created_day(value: Any) -> str:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    return value[:10] if isinstance(value, str) else ""


def question_stats(db: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
    """Rapor verisi: Sorular listesi/sayacıyla aynı küme (created_at'i olan belgeler), tüm sürümler.
    Sınıflandırma alanları sınırlı taramayla (COUNT_SCAN_CAP, 60 sn önbellek) tek geçişte çekilir;
    gruplama ve filtreleme istemcide yapılır."""
    from database.question_report import QUESTION_REPORT_FIELDS, normalize_question_report_record

    collection = _collection(params.get("collection"))
    key = ("stats", collection)
    now = time.monotonic()
    with _COUNT_LOCK:
        hit = _COUNT_CACHE.get(key)
    if hit and now - hit[0] < COUNT_CACHE_TTL:
        return {**hit[1], "cached": True}

    query = db.collection(collection).order_by("created_at", direction="DESCENDING")
    query = query.select(sorted({*QUESTION_REPORT_FIELDS, "created_at", "supported_languages"}))
    records: List[Dict[str, Any]] = []
    scanned, truncated = 0, False
    while not truncated:
        size = min(SCAN_BATCH * 5, COUNT_SCAN_CAP + 1 - scanned)  # one extra read decides exactness
        batch = query.limit(size).get()
        for snapshot in batch:
            if scanned >= COUNT_SCAN_CAP:
                truncated = True
                break
            scanned += 1
            data = snapshot.to_dict() or {}
            record = normalize_question_report_record(data, snapshot.id)
            record.update(
                version=_text(data.get("dbVersion")) or _text(data.get("version")) or "—",
                created=_created_day(data.get("created_at")),
                scope="global" if record["country_code"] == "GL" else "local",
                languages=[code for code in (data.get("supported_languages") or []) if isinstance(code, str)],
            )
            records.append(record)
        if truncated or len(batch) < size:
            break
        query = query.start_after(batch[-1])
    result = {
        "collection": collection,
        "total": len(records),
        "exact": not truncated,
        "scanned": scanned,
        "versions": sorted({record["version"] for record in records}, reverse=True),
        "questions": records,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with _COUNT_LOCK:
        _COUNT_CACHE[key] = (now, result)
    return {**result, "cached": False}
