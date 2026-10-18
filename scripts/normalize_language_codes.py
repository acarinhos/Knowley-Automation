"""Normalise language keys of stored questions to the short canonical codes (fr-FR -> fr, es-419 -> es-LA, ...).

Dry-run by default (read-only report). --apply needs --project-id knowley-1, writes a local backup first
(local_backups/, not committed), records finished documents for --resume and reports every document.

Rules per document (translations, supported_languages and the nested payload copy):
- an old key without a short twin is moved to the canonical key;
- when both exist the SHORT key's content is kept; only fields missing/empty there are filled from the old key,
  then the old key is removed;
- unknown keys (am, sr, tzm, ...) are left untouched; nothing else in the document changes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.translation_service import LANGUAGE_CODE_ALIASES, canonical_language_code

BACKUP_DIR = ROOT / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "normalize_language_progress.json"
logger = logging.getLogger("NormalizeLanguageCodes")
_DELETE = "__DELETE__"  # replaced by firestore.DELETE_FIELD when writing


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, (dict, list)) and not value)


def merge_missing(keep: Any, other: Any) -> Any:
    """`keep` wins; empty parts of it are filled from `other` (recursively for dicts)."""
    if isinstance(keep, Mapping) and isinstance(other, Mapping):
        merged = dict(keep)
        for key, value in other.items():
            merged[key] = merge_missing(keep[key], value) if key in keep else value
        return merged
    return other if _empty(keep) else keep


def plan_translations(translations: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """New translations map and stats {renamed, merged, conflicts}. Unknown keys stay as they are."""
    stats = {"renamed": 0, "merged": 0, "conflicts": 0}
    result: Dict[str, Any] = {}
    # short (already canonical) keys first so they win over regional spellings
    for key in sorted(translations, key=lambda k: canonical_language_code(k) != k, reverse=False):
        target = canonical_language_code(key)
        content = translations[key]
        if target == key:
            result[key] = content
        elif target in result:
            stats["merged"] += 1
            old_q, new_q = (content or {}).get("question"), (result[target] or {}).get("question")
            if isinstance(old_q, str) and isinstance(new_q, str) and old_q.strip() != new_q.strip():
                stats["conflicts"] += 1
            result[target] = merge_missing(result[target], content)
        else:
            stats["renamed"] += 1
            result[target] = content
    return result, stats


def plan_languages(languages: List[str]) -> List[str]:
    return list(dict.fromkeys(canonical_language_code(code) for code in languages))


def plan_doc(data: Mapping[str, Any]) -> Dict[str, Any]:
    """{"set": {path tuple: value}, "delete": [path tuple], "stats": {...}} for one document ({} when nothing to do)."""
    sets: Dict[tuple, Any] = {}
    deletes: List[tuple] = []
    totals = {"renamed": 0, "merged": 0, "conflicts": 0, "languages": 0}

    def handle(prefix: tuple, container: Mapping[str, Any]) -> None:
        translations = container.get("translations")
        if isinstance(translations, Mapping):
            new, stats = plan_translations(translations)
            for key in translations:
                if key not in new:
                    deletes.append((*prefix, "translations", key))
            for key, value in new.items():
                if translations.get(key) != value:
                    sets[(*prefix, "translations", key)] = value
            for name, count in stats.items():
                totals[name] += count
        languages = container.get("supported_languages")
        if isinstance(languages, list):
            new_languages = plan_languages(languages)
            if new_languages != languages:
                sets[(*prefix, "supported_languages")] = new_languages
                totals["languages"] += 1

    handle((), data)
    if isinstance(data.get("payload"), Mapping):
        handle(("payload",), data["payload"])
    if not sets and not deletes:
        return {}
    return {"set": sets, "delete": deletes, "stats": totals}


def mapping_table() -> List[Tuple[str, str]]:
    return sorted(LANGUAGE_CODE_ALIASES.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import firebase_admin
    from firebase_admin import firestore
    from google.cloud.firestore_v1._helpers import LastUpdateOption
    from google.cloud.firestore_v1.field_path import get_field_path

    from database.db_manager import init_firebase

    db = init_firebase()
    if args.apply and firebase_admin.get_app().project_id != args.project_id:
        raise SystemExit("Refusing: initialized Firebase project differs from --project-id.")
    done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if args.resume and PROGRESS_FILE.exists() else set()

    plan = []
    totals: Counter = Counter()
    key_counts: Counter = Counter()
    conflict_docs: List[str] = []
    scanned = 0
    for snap in db.collection("questions").stream():
        scanned += 1
        if snap.id in done:
            continue
        data = snap.to_dict() or {}
        doc_plan = plan_doc(data)
        if not doc_plan:
            continue
        plan.append((snap, data, doc_plan))
        totals.update(doc_plan["stats"])
        for path in doc_plan["delete"]:
            key_counts[path[-1]] += 1
        if doc_plan["stats"]["conflicts"]:
            conflict_docs.append(snap.id)
    if args.limit > 0:
        plan = plan[: args.limit]

    logger.info("Scanned %d docs; docs to change: %d; renamed keys %d, merged duplicate keys %d (content differs in %d docs), "
                "supported_languages rewritten in %d places", scanned, len(plan), totals["renamed"], totals["merged"],
                len(conflict_docs), totals["languages"])
    logger.info("Old keys removed (top): %s", dict(key_counts.most_common(40)))
    logger.info("Mapping table (old -> canonical): %s", dict(mapping_table()))
    if conflict_docs:
        logger.info("Docs where short and regional key had DIFFERENT questions (short kept): %s", conflict_docs[:10])
    if not args.apply:
        logger.info("Dry run only. Nothing was written.")
        return 0

    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"normalize_language_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps({s.id: d for s, d, _ in plan}, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Backup written: %s", backup)
    states: Dict[str, str] = {}
    done_now = set(done)
    for snap, _data, doc_plan in plan:
        try:
            payload: Dict[str, Any] = {get_field_path(list(path)): value for path, value in doc_plan["set"].items()}
            payload.update({get_field_path(list(path)): firestore.DELETE_FIELD for path in doc_plan["delete"]})
            snap.reference.update(payload, option=LastUpdateOption(snap.update_time))
            done_now.add(snap.id)
            states[snap.id] = "written"
        except Exception as exc:
            states[snap.id] = f"skipped: {type(exc).__name__}: {str(exc)[:100]}"
            logger.warning("%s -> %s", snap.id, states[snap.id])
    PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    logger.info("Result: %s", dict(Counter("skipped" if v.startswith("skipped") else v for v in states.values())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
