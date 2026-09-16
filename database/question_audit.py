"""Firestore questions koleksiyonu için v1.1.0+ üretim denetim özeti."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
ISTANBUL = timezone(timedelta(hours=3), name="Europe/Istanbul")


def _version_tuple(value: Any) -> Tuple[int, ...]:
    parts = str(value or "0").strip().split(".")
    result = []
    for part in parts:
        digits = "".join(char for char in part if char.isdigit())
        result.append(int(digits or 0))
    return tuple((result + [0, 0, 0])[:3])


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ISTANBUL)


def build_question_audit(
    documents: Iterable[Mapping[str, Any]],
    *,
    minimum_version: str = "1.1.0",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now_local = (now or datetime.now(timezone.utc)).astimezone(ISTANBUL)
    today = now_local.date()
    week_start = today.fromordinal(today.toordinal() - today.weekday())
    month_key = (today.year, today.month)
    minimum = _version_tuple(minimum_version)
    rows: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(
        lambda: {"today": 0, "week": 0, "month": 0, "all_time": 0}
    )
    totals = {"today": 0, "week": 0, "month": 0, "all_time": 0, "undated": 0}
    scanned = 0

    for data in documents:
        scanned += 1
        version = data.get("dbVersion") or data.get("version")
        if _version_tuple(version) < minimum:
            continue
        categories = data.get("categories") or [data.get("category") or "Bilinmeyen"]
        subcategories = data.get("sub_categories") or [data.get("subcategory") or "Bilinmeyen"]
        category = str(categories[0] if isinstance(categories, list) else categories)
        subcategory = str(subcategories[0] if isinstance(subcategories, list) else subcategories)
        row = rows[(category, subcategory)]
        row["all_time"] += 1
        totals["all_time"] += 1

        created = _as_datetime(data.get("created_at"))
        if created is None:
            totals["undated"] += 1
            continue
        created_date = created.date()
        if created_date == today:
            row["today"] += 1
            totals["today"] += 1
        if week_start <= created_date <= today:
            row["week"] += 1
            totals["week"] += 1
        if (created_date.year, created_date.month) == month_key:
            row["month"] += 1
            totals["month"] += 1

    table = [
        {"category": category, "subcategory": subcategory, **counts}
        for (category, subcategory), counts in rows.items()
    ]
    table.sort(key=lambda item: (-item["all_time"], item["category"], item["subcategory"]))
    return {
        "minimum_version": minimum_version,
        "scanned": scanned,
        "totals": totals,
        "rows": table,
        "generated_at": now_local.isoformat(),
    }


def fetch_question_audit(db: Any, minimum_version: str = "1.1.0") -> Dict[str, Any]:
    query = db.collection("questions").select(
        ["dbVersion", "version", "created_at", "categories", "category", "sub_categories", "subcategory"]
    )
    documents = (snapshot.to_dict() or {} for snapshot in query.stream())
    return build_question_audit(documents, minimum_version=minimum_version)
