"""Authenticated user intake for question-generation requests."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from firebase_admin import firestore

from core.translation_service import SUPPORTED_LANGUAGE_CODES


REQUESTS_COLLECTION = "question_generation_requests"
RATE_LIMIT_COLLECTION = "question_generation_request_limits"
MAX_REQUEST_TEXT = 1200
MAX_SOURCE_URLS = 3
DEFAULT_DAILY_LIMIT = 5
ALLOWED_SCOPE_PREFERENCES = {"global", "local"}


class RequestRateLimitExceeded(RuntimeError):
    """Raised when a user exceeds the configured daily request quota."""


def _request_daily_limit() -> int:
    try:
        value = int(os.getenv("QUESTION_REQUESTS_DAILY_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    except (TypeError, ValueError):
        value = DEFAULT_DAILY_LIMIT
    return max(1, min(value, 100))


def validate_question_request(
    payload: Mapping[str, Any], repository: Any
) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("İstek gövdesi JSON nesnesi olmalıdır.")

    allowed = {
        "request_text",
        "desired_answer",
        "category",
        "subcategory",
        "scope_preference",
        "country_code",
        "language_code",
        "source_urls",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("Desteklenmeyen alan gönderildi.")

    request_text = str(payload.get("request_text") or "").strip()
    if not 12 <= len(request_text) <= MAX_REQUEST_TEXT:
        raise ValueError(f"Soru fikri 12–{MAX_REQUEST_TEXT} karakter olmalıdır.")

    desired_answer = str(payload.get("desired_answer") or "").strip()
    if len(desired_answer) > 500:
        raise ValueError("Beklenen cevap en fazla 500 karakter olabilir.")

    category = str(payload.get("category") or "").strip()
    subcategory = str(payload.get("subcategory") or "").strip()
    categories = set(repository.categories())
    if not category:
        raise ValueError("Ana kategori seçilmelidir.")
    if category not in categories:
        raise ValueError("Kategori, güncel kategori listesinden seçilmelidir.")
    if subcategory and not category:
        raise ValueError("Alt kategori seçmek için önce kategori seçilmelidir.")

    scope = str(payload.get("scope_preference") or "global").strip().lower()
    if scope not in ALLOWED_SCOPE_PREFERENCES:
        raise ValueError("Kapsam tercihi global veya local olmalıdır.")

    country_code = str(payload.get("country_code") or "").strip().upper()
    country = next(
        (item for item in repository.countries() if str(item.get("code") or "").upper() == country_code),
        None,
    ) if country_code else None
    if country_code and not country:
        raise ValueError("Ülke/bölge kodu güncel Sheets listesinden seçilmelidir.")
    if scope == "local" and not country:
        raise ValueError("Yerel talepte ülke/bölge seçilmelidir.")
    if scope == "global" and country:
        raise ValueError("Global talepte ülke/bölge seçilmemelidir.")

    requested_language = str(payload.get("language_code") or "auto").strip()
    language_by_casefold = {code.casefold(): code for code in SUPPORTED_LANGUAGE_CODES}
    language_code = language_by_casefold.get(requested_language.casefold(), requested_language)
    if language_code != "auto" and language_code not in SUPPORTED_LANGUAGE_CODES:
        raise ValueError("Dil, desteklenen dillerden biri olmalıdır veya otomatik seçilmelidir.")
    if country and language_code != "auto" and language_code not in set(country.get("languages") or []):
        raise ValueError("Seçilen dil bu ülke/bölge için Sheets dil listesinde bulunmuyor.")

    if subcategory:
        if not category:
            raise ValueError("Alt kategori seçmek için önce kategori seçilmelidir.")
        available_subcategories = repository.subcategories(
            category, country.get("name") if country else None
        )
        if not any(item.get("subcategory") == subcategory for item in available_subcategories):
            raise ValueError("Alt kategori, seçilen kapsam ve ülke/bölge için geçerli değil.")

    source_urls = payload.get("source_urls") or []
    if not isinstance(source_urls, list) or len(source_urls) > MAX_SOURCE_URLS:
        raise ValueError(f"En fazla {MAX_SOURCE_URLS} kaynak bağlantısı eklenebilir.")
    normalized_urls = []
    for raw_url in source_urls:
        url = str(raw_url or "").strip()
        parsed = urlparse(url)
        if len(url) > 2048 or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Kaynak bağlantıları geçerli HTTPS adresleri olmalıdır.")
        normalized_urls.append(url)

    return {
        "request_text": request_text,
        "desired_answer": desired_answer or None,
        "category": category or None,
        "subcategory": subcategory or None,
        "scope_preference": scope,
        "country_code": country_code or None,
        "country_name": str(country.get("name")) if country else None,
        "language_code": language_code,
        "source_urls": normalized_urls,
    }


def create_question_request(
    db: Any,
    *,
    user_uid: str,
    payload: Mapping[str, Any],
    repository: Any,
) -> Dict[str, Any]:
    """Validate and atomically add a queued request plus per-user daily quota."""
    normalized = validate_question_request(payload, repository)
    now = datetime.now(timezone.utc)
    day_key = now.strftime("%Y%m%d")
    request_id = uuid.uuid4().hex
    request_ref = db.collection(REQUESTS_COLLECTION).document(request_id)
    limit_ref = db.collection(RATE_LIMIT_COLLECTION).document(f"{user_uid}_{day_key}")
    daily_limit = _request_daily_limit()

    request_document = {
        "schema_version": 1,
        "request_id": request_id,
        "user_uid": user_uid,
        **normalized,
        "status": "queued",
        "duplicate_check": {"status": "pending", "matches": []},
        "result_question_id": None,
        "failure_code": None,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    transaction = db.transaction()

    @firestore.transactional
    def reserve_and_create(tx: Any) -> None:
        snapshot = limit_ref.get(transaction=tx)
        used = int(snapshot.get("request_count") or 0) if snapshot.exists else 0
        if used >= daily_limit:
            raise RequestRateLimitExceeded("Günlük soru talebi sınırına ulaşıldı.")
        tx.create(request_ref, request_document)
        tx.set(
            limit_ref,
            {
                "user_uid": user_uid,
                "day": day_key,
                "request_count": used + 1,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    reserve_and_create(transaction)
    return {"request_id": request_id, "status": "queued"}


def get_question_request_status(
    db: Any, *, request_id: str, user_uid: str, is_admin: bool = False
) -> Dict[str, Any]:
    snapshot = db.collection(REQUESTS_COLLECTION).document(request_id).get()
    if not snapshot.exists:
        raise KeyError(request_id)
    document = snapshot.to_dict() or {}
    if not is_admin and document.get("user_uid") != user_uid:
        raise PermissionError("Bu soru talebini görme yetkiniz yok.")
    return {
        "request_id": request_id,
        "status": document.get("status", "queued"),
        "duplicate_check": document.get("duplicate_check") or {"status": "pending", "matches": []},
        "result_question_id": document.get("result_question_id"),
        "failure_code": document.get("failure_code"),
    }


def list_question_requests(
    db: Any, *, status: Optional[str] = None, limit: int = 50
) -> Dict[str, Any]:
    query = db.collection(REQUESTS_COLLECTION)
    if status:
        query = query.where("status", "==", status)
    query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(max(1, min(limit, 100)))
    items = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        items.append({"request_id": snapshot.id, **data})
    return {"items": items, "count": len(items)}
