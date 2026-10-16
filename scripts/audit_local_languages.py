"""Read-only audit: do active LOCAL questions carry their country's Sheets languages + English?

Run: python -m scripts.audit_local_languages
Extra languages are fine (never removed); only MISSING required languages are reported.
Writes local_backups/local_language_gaps.json and prints counts per country and per language.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local_backups" / "local_language_gaps.json"

# Equivalent codes seen in Sheets vs stored translations -> canonical Sheets form.
ALIASES = {
    "tr": "tr-TR", "de": "de-DE", "fr": "fr-FR", "it": "it-IT", "es": "es-ES", "ru": "ru-RU", "ja": "ja-JP",
    "ko": "ko-KR", "pl": "pl-PL", "hu": "hu-HU", "ms": "ms-MY", "pt": "pt-BR", "el": "el-GR", "cs": "cs-CZ",
    "sv": "sv-SE", "da": "da-DK", "fi": "fi-FI", "nl": "nl-NL", "hi": "hi-IN", "bn": "bn-BD", "fa": "fa-IR",
    "he": "iw-IL", "iw": "iw-IL", "nb": "no-NO", "no": "no-NO", "zh-hans": "zh-CN", "zh-hant": "zh-TW",
    "zh": "zh-CN",
}


# Legacy English country names stored on old questions -> Sheets country name.
COUNTRY_ALIASES = {"spain": "İspanya", "france": "Fransa", "germany": "Almanya", "italy": "İtalya", "turkey": "Türkiye",
                   "japan": "Japonya", "china": "Çin", "russia": "Rusya", "greece": "Yunanistan", "india": "Hindistan",
                   "united states": "ABD", "usa": "ABD", "canada": "Kanada", "united kingdom": "Birleşik Krallık"}


def resolve_country(data: Mapping[str, Any], repository: Any, fallback: Any) -> Optional[Mapping[str, Any]]:
    """Country row for a question: the shared resolver first, then legacy English names."""
    found = fallback(data, repository)
    if found is not None:
        return found
    by_name = {item["name"]: item for item in repository.countries()}
    names = [data.get("target_country"), *(data.get("countries") or [])]
    for name in names:
        alias = COUNTRY_ALIASES.get(str(name or "").strip().lower())
        if alias in by_name:
            return by_name[alias]
    return None


def canon(code: Any) -> str:
    text = str(code or "").strip()
    return ALIASES.get(text.lower(), text if "-" not in text else f"{text.split('-')[0].lower()}-{text.split('-')[1]}")


def usable(content: Any) -> bool:
    options = content.get("options") if isinstance(content, Mapping) else None
    return (isinstance(content, Mapping) and bool(str(content.get("question") or "").strip())
            and isinstance(options, Mapping) and all(str(options.get(k) or "").strip() for k in "ABCD"))


def missing_languages(data: Mapping[str, Any], required: List[str]) -> List[str]:
    """Required (Sheets-form) language codes with no usable translation under an equivalent key."""
    present = {canon(loc) for loc, content in (data.get("translations") or {}).items() if usable(content)}
    return [lang for lang in required if canon(lang) not in present]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from core.sheet_algorithm import get_sheet_algorithm_repository
    from database.db_manager import init_firebase
    from scripts.backfill_v110_translations import _country_for_question

    repository = get_sheet_algorithm_repository()
    db = init_firebase()
    gaps: List[Dict[str, Any]] = []
    unresolved: List[str] = []
    by_country: Counter = Counter()
    by_language: Counter = Counter()
    scanned = 0
    for snap in db.collection("questions").stream():
        data = snap.to_dict() or {}
        if str(data.get("scope") or "").lower() != "local" or data.get("is_active") is False:
            continue
        scanned += 1
        country = resolve_country(data, repository, _country_for_question)
        if country is None:
            unresolved.append(snap.id)
            continue
        required = repository.question_languages(country["name"])
        missing = missing_languages(data, required)
        if missing:
            gaps.append({"doc_id": snap.id, "country": country["name"], "country_code": country["code"],
                         "missing": missing, "present": sorted(data.get("translations") or {})})
            by_country[country["name"]] += 1
            by_language.update(missing)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"gaps": gaps, "unresolved_country": unresolved,
                               "by_country": dict(by_country), "by_language": dict(by_language)}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"Active local questions scanned: {scanned}; with gaps: {len(gaps)}; country unresolved: {len(unresolved)}")
    print("By country:", dict(by_country.most_common()))
    print("By missing language:", dict(by_language.most_common()))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
