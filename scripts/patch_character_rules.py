"""Narrow patch of the `characters` collection. Dry-run by default.

Does exactly two things per character document and nothing else (avatar_url, avatar_urls, schema_version, names,
translations, skills, ... are never touched):
  1. daily_reset_hour_local = 10   (skipped when already 10)
  2. delete the field source.display_order   (skipped when absent)

  python -m scripts.patch_character_rules --project-id knowley-1            dry-run
  python -m scripts.patch_character_rules --project-id knowley-1 --apply    write (backup first, resumable)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.character_catalog import DAILY_RESET_HOUR_LOCAL

BACKUP_DIR = ROOT / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "patch_character_rules_progress.json"
DELETE = "__DELETE_FIELD__"


def plan_patch(current: Mapping[str, Any]) -> Dict[str, Any]:
    """Field-path -> new value (DELETE removes the field). Empty when the document is already patched."""
    update: Dict[str, Any] = {}
    if current.get("daily_reset_hour_local") != DAILY_RESET_HOUR_LOCAL:
        update["daily_reset_hour_local"] = DAILY_RESET_HOUR_LOCAL
    source = current.get("source")
    if isinstance(source, Mapping) and "display_order" in source:
        update["source.display_order"] = DELETE
    return update


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import firebase_admin
    from firebase_admin import firestore

    from database.db_manager import init_firebase

    db = init_firebase()
    if firebase_admin.get_app().project_id != args.project_id:
        raise SystemExit("Refusing: initialized Firebase project differs from --project-id.")
    done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if args.resume and PROGRESS_FILE.exists() else set()
    snapshots = {snap.id: snap for snap in db.collection("characters").stream()}
    plan = []
    for doc_id, snap in sorted(snapshots.items()):
        update = plan_patch(snap.to_dict() or {})
        if update and doc_id not in done:
            plan.append((doc_id, update))
    counts: Dict[str, int] = {}
    for _id, update in plan:
        for field in update:
            counts[field] = counts.get(field, 0) + 1
    print(f"Project {args.project_id}: {len(snapshots)} character documents, {len(plan)} to patch, "
          f"{len(snapshots) - len(plan)} already fine/skipped; fields: {counts}")
    if not args.apply:
        print("Dry run only. Nothing was written.")
        return 0

    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"patch_character_rules_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps({i: snapshots[i].to_dict() for i, _ in plan}, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Backup written: {backup}")
    states: Dict[str, str] = {}
    done_now = set(done)
    for start in range(0, len(plan), 400):
        chunk = plan[start:start + 400]
        try:
            batch = db.batch()
            for doc_id, update in chunk:
                payload = {k: (firestore.DELETE_FIELD if v == DELETE else v) for k, v in update.items()}
                batch.update(db.collection("characters").document(doc_id), payload)
            batch.commit()
            for doc_id, _ in chunk:
                states[doc_id] = "written"
                done_now.add(doc_id)
        except Exception as exc:
            for doc_id, _ in chunk:
                states[doc_id] = f"skipped: {type(exc).__name__}: {str(exc)[:100]}"
        PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    bad = 0
    for doc_id, state in states.items():
        if state != "written":
            print(f"{doc_id} -> {state}")
            continue
        after = db.collection("characters").document(doc_id).get().to_dict() or {}
        if after.get("daily_reset_hour_local") != DAILY_RESET_HOUR_LOCAL or "display_order" in (after.get("source") or {}):
            bad += 1
            print(f"Verification failed: {doc_id}")
    written = sum(1 for s in states.values() if s == "written")
    print(f"Result: {written} written, {len(states) - written} skipped, {bad} verification failures.")
    return 1 if bad or written != len(states) else 0


if __name__ == "__main__":
    raise SystemExit(main())
