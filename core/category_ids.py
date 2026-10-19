"""Stable category / sub-category ids for questions and the legacy-name -> current-name mapping.

Ids use the same slug as the characters (`core.character_catalog.slug_id` of the CURRENT Turkish name), so a question and a
character of the same category share `category_id`. `category_ids` / `sub_category_ids` are lists parallel to
`categories` / `sub_categories`.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from config.categories_config import SHEET_CATEGORY_COLORS, get_category_color
from core.character_catalog import slug_id

CURRENT_CATEGORIES: Tuple[str, ...] = tuple(SHEET_CATEGORY_COLORS)  # the 21 Sheets categories

# Legacy / alternative names that map to exactly ONE current category (1:1 or an unambiguous merge).
LEGACY_TO_CURRENT: Dict[str, str] = {
    "Ekonomi & Finans": "Ekonomi", "Finans": "Ekonomi",
    "Felsefe & Mantık": "Felsefe", "Felsefe ve Mantık": "Felsefe",
    "Sinema & Dizi": "Sinema", "Diziler": "Sinema",
    "Genel Kültür & Mitoloji": "Genel Kültür", "Genel": "Genel Kültür",
    "Bilgisayar & Yazılım": "Teknoloji", "Yazılım": "Teknoloji", "Bilgisayar": "Teknoloji",
    "Tıp & Sağlık": "Sağlık", "Tıp": "Sağlık",
    "Sosyoloji & Psikoloji": "Psikoloji", "Sosyoloji": "Psikoloji",
    "Astronomi & Uzay": "Astronomi", "Uzay": "Astronomi",
    "Hukuk & Siyaset": "Siyaset", "Hukuk": "Siyaset",
    "Oyun & Espor": "Oyun", "Video Oyunları": "Oyun", "Espor": "Oyun",
    "Gastronomi & Mutfak": "Gastronomi", "Mutfak": "Gastronomi",
    "Mimarlık & Sanat": "Resim", "Mimarlık": "Resim", "Mimari": "Resim", "Sanat": "Resim",
}
# One old name that could belong to several current categories: never auto-mapped (manual review).
AMBIGUOUS_LEGACY: Dict[str, Tuple[str, ...]] = {
    "Sanat ve Edebiyat": ("Edebiyat", "Resim"),
    "Bilim": ("Fizik", "Kimya", "Biyoloji", "Astronomi"),
    "Popüler Kültür": ("Sinema", "Müzik", "Genel Kültür"),
}


def resolve_category(name: Any) -> Tuple[str, Optional[str]]:
    """(status, current_name): 'current' | 'mapped' | 'ambiguous' | 'unmapped'; current_name is None unless usable."""
    text = str(name or "").strip()
    if text in CURRENT_CATEGORIES:
        return "current", text
    if text in LEGACY_TO_CURRENT:
        return "mapped", LEGACY_TO_CURRENT[text]
    if text in AMBIGUOUS_LEGACY:
        return "ambiguous", None
    return "unmapped", None


def category_id(name: str) -> str:
    return slug_id(name)


def category_ids_for(categories: Iterable[str], sub_categories: Iterable[str] = ()) -> Tuple[List[str], List[str]]:
    """Parallel id lists for the (already normalised) category and sub-category names."""
    return [slug_id(c) for c in categories], [slug_id(s) for s in sub_categories]


def apply_category_ids(question: Dict[str, Any]) -> bool:
    """Add category_ids / sub_category_ids to a question dict when missing or not parallel; True when changed."""
    categories = [str(c) for c in (question.get("categories") or [])]
    subs = [str(s) for s in (question.get("sub_categories") or [])]
    ids, sub_ids = category_ids_for(categories, subs)
    changed = question.get("category_ids") != ids or question.get("sub_category_ids") != sub_ids
    question["category_ids"], question["sub_category_ids"] = ids, sub_ids
    return changed


def desired_category_fields(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Planned values for one stored question: categories/meta names + colours (current names) and the id lists.

    Returns {"status": "ok"|"manual", "reason": ..., "set": {...}}. Documents with an ambiguous/unmapped category name
    are left alone ('manual'); a document is never partially fixed."""
    categories = [str(c) for c in (data.get("categories") or [])]
    resolved = [resolve_category(c) for c in categories]
    bad = [c for c, (status, _) in zip(categories, resolved) if status in ("ambiguous", "unmapped")]
    if bad or not categories:
        return {"status": "manual", "reason": ", ".join(bad) or "no categories", "set": {}}
    current = [name for _, name in resolved]
    subs = [str(s) for s in (data.get("sub_categories") or [])]
    ids, sub_ids = category_ids_for(current, subs)
    target = {
        "categories": current,
        "categories_meta": [{"name": name, "color": get_category_color(name)} for name in current],
        "category_ids": ids,
        "sub_category_ids": sub_ids,
    }
    changes = {key: value for key, value in target.items() if data.get(key) != value}
    return {"status": "ok", "reason": "", "set": changes}
