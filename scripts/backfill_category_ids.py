"""Add category_ids / sub_category_ids to stored questions and fix legacy category names. Dry-run by default.

  python -m scripts.backfill_category_ids                              read-only report
  python -m scripts.backfill_category_ids --apply --project-id knowley-1  write (backup, resume, per-document report)

Only these fields are written (merge-only, update-time precondition): category_ids, sub_category_ids and - when a
category name can be mapped unambiguously to the current Sheets name - categories and categories_meta (current name
+ the Sheets colour). question_hash, translations, is_active, ... are never touched. Documents with an ambiguous or
unknown category name are skipped completely and listed for manual review.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.category_ids import CURRENT_CATEGORIES, LEGACY_TO_CURRENT, category_id, desired_category_fields, resolve_category
from core.character_catalog import slug_id

BACKUP_DIR = ROOT / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "category_ids_progress.json"
WRITE_FIELDS = ("categories", "categories_meta", "category_ids", "sub_category_ids")
logger = logging.getLogger("CategoryIds")


def version_of(data: Mapping[str, Any]) -> str:
    return str(data.get("dbVersion") or data.get("version") or "?")


def version_group(version: str) -> str:
    return "1.1.0" if version == "1.1.0" else "1.0.x/other"


def classify_changes(data: Mapping[str, Any], planned: Mapping[str, Any]) -> Dict[str, bool]:
    """Which kinds of change a planned update contains."""
    categories_changed = "categories" in planned
    return {
        "name_fix": categories_changed,
        "color_fix": "categories_meta" in planned and not categories_changed,
        "ids_added": "category_ids" in planned or "sub_category_ids" in planned,
    }


def dictionary_report(repository: Any) -> Dict[str, Any]:
    """Category name -> id (21) and the sub-category dictionary integrity (slug collisions between different names)."""
    names = set()
    for item in [None, *[c["name"] for c in repository.countries()]]:
        names.update(str(r["subcategory"]) for r in repository.records_for_country(item) if r.get("subcategory"))
    by_slug: Dict[str, set] = defaultdict(set)
    for name in names:
        by_slug[slug_id(name)].add(name)
    return {
        "categories": {name: category_id(name) for name in CURRENT_CATEGORIES},
        "sub_category_names": len(names),
        "sub_category_ids": len(by_slug),
        "slug_collisions": {slug: sorted(group) for slug, group in by_slug.items() if len(group) > 1},
        "empty_slugs": sorted(name for name in names if not slug_id(name)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import firebase_admin
    from google.cloud.firestore_v1._helpers import LastUpdateOption

    from core.sheet_algorithm import get_sheet_algorithm_repository
    from database.db_manager import init_firebase

    db = init_firebase()
    if args.apply and firebase_admin.get_app().project_id != args.project_id:
        raise SystemExit("Refusing: initialized Firebase project differs from --project-id.")
    done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if args.resume and PROGRESS_FILE.exists() else set()

    totals: Dict[str, Counter] = defaultdict(Counter)  # group -> counters
    mapping: Counter = Counter()  # (group, old, new) -> docs
    manual: List[Dict[str, Any]] = []
    filter_tag_legacy: Counter = Counter()
    plan: List[Tuple[Any, Dict[str, Any]]] = []
    for snap in db.collection("questions").stream():
        data = snap.to_dict() or {}
        group = version_group(version_of(data))
        totals[group]["docs"] += 1
        for content in (data.get("translations") or {}).values():
            tag = (content or {}).get("filter_tag") if isinstance(content, dict) else None
            if tag in LEGACY_TO_CURRENT:
                filter_tag_legacy[(group, tag)] += 1
        result = desired_category_fields(data)
        if result["status"] == "manual":
            totals[group]["manual"] += 1
            manual.append({"doc_id": snap.id, "version": version_of(data), "categories": data.get("categories"), "reason": result["reason"]})
            continue
        planned = result["set"]
        if not planned:
            totals[group]["already_ok"] += 1
            continue
        kinds = classify_changes(data, planned)
        for kind, flag in kinds.items():
            totals[group][kind] += int(flag)
        for old, new in zip(data.get("categories") or [], planned.get("categories") or data.get("categories") or []):
            if old != new:
                mapping[(group, old, new)] += 1
        if snap.id not in done:
            plan.append((snap, planned))

    stats = {
        "documents": {g: dict(c) for g, c in totals.items()},
        "to_write": len(plan),
        "legacy_to_current": {f"[{g}] {o} -> {n}": c for (g, o, n), c in sorted(mapping.items())},
        "manual_review": len(manual),
        "legacy_names_in_filter_tag (not changed)": {f"[{g}] {t}": c for (g, t), c in sorted(filter_tag_legacy.items())},
        "dictionary": dictionary_report(get_sheet_algorithm_repository()),
    }
    BACKUP_DIR.mkdir(exist_ok=True)
    (BACKUP_DIR / "category_ids_manual_review.json").write_text(json.dumps(manual, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    if not args.apply:
        print("Dry run only. Manual-review list: local_backups/category_ids_manual_review.json. Nothing was written.")
        return 0

    backup = BACKUP_DIR / f"category_ids_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps({s.id: {k: (s.to_dict() or {}).get(k) for k in WRITE_FIELDS} for s, _ in plan},
                                 ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Backup (previous values of the changed fields) written: {backup}")
    states: Dict[str, str] = {}
    done_now = set(done)
    for snap, planned in plan:
        try:
            snap.reference.update(dict(planned), option=LastUpdateOption(snap.update_time))
            states[snap.id] = "written"
            done_now.add(snap.id)
        except Exception as exc:
            states[snap.id] = f"skipped: {type(exc).__name__}: {str(exc)[:100]}"
        if len(states) % 200 == 0:
            PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    failed = {k: v for k, v in states.items() if v.startswith("skipped")}
    for doc_id, state in failed.items():
        print(f"{doc_id} -> {state}")
    print(f"Result: {len(states) - len(failed)} written, {len(failed)} skipped.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
