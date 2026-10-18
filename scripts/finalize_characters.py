"""Bring Firestore `characters` in line with the final catalogue. Dry-run by default.

  python -m scripts.finalize_characters --project-id knowley-1                    dry-run report
  python -m scripts.finalize_characters --project-id knowley-1 --apply            write (backup first, resumable)
  python -m scripts.finalize_characters --project-id knowley-1 --upload-avatar [--apply]
                                                                                  shared robot avatar -> Storage

Per existing document (ids never change; users/* is never touched):
  name, title, role, skills text/effects from the alias catalogue; translations moved to the short language codes
  (the short key wins on duplicates, extra fields are kept); `rarity` deleted; is_active=true; avatar_url/avatar_urls (per-level);
  unlock_threshold=50; level_thresholds; schema_version.
Missing catalogue characters are created (with created_at). --delete-orphans removes documents that are not in the
catalogue (backed up first; refused while users/*/characters references them). Nothing else is deleted.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.character_catalog import (
    CHARACTER_SCHEMA_VERSION, DEFAULT_AVATAR_PATH, DEFAULT_BUCKET, default_avatar_url, firestore_character_document,
    load_global_character_catalog,
)
from core.translation_service import canonical_language_code

BACKUP_DIR = ROOT / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "finalize_characters_progress.json"
AVATAR_FILE = ROOT / "data" / "default_robot.png"
DELETE = "__DELETE_FIELD__"


def migrate_translations(current: Mapping[str, Any], expected: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Short-code translations: regional keys move to their short code, the short key wins, extra fields survive,
    and name/title/role come from the catalogue."""
    merged: Dict[str, Dict[str, Any]] = {}
    for key in sorted(current, key=lambda k: canonical_language_code(k) != k):  # short (canonical) keys first
        content = dict(current[key] or {}) if isinstance(current[key], Mapping) else {}
        target = canonical_language_code(key)
        base = merged.setdefault(target, {})
        for field, value in content.items():
            if field not in base or base[field] in (None, ""):
                base[field] = value
    for locale, values in expected.items():
        merged.setdefault(locale, {}).update({k: values[k] for k in ("name", "title", "role") if k in values})
    return merged


def merge_skills(current: List[Mapping[str, Any]], expected: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Catalogue values win per skill (matched by skill_id); fields only present in Firestore are kept."""
    by_id = {skill.get("skill_id"): skill for skill in current or []}
    result = []
    for skill in expected:
        merged = copy.deepcopy(dict(by_id.get(skill["skill_id"], {})))
        merged.update(copy.deepcopy(dict(skill)))
        result.append(merged)
    return result


def plan_character_update(current: Mapping[str, Any], expected: Mapping[str, Any]) -> Dict[str, Any]:
    """Changed top-level fields only; DELETE marks a field to remove. {} when the document is already final."""
    update: Dict[str, Any] = {}
    for field in ("name", "title", "role", "avatar_url", "avatar_urls", "unlock_threshold", "level_thresholds", "schema_version"):
        if current.get(field) != expected[field]:
            update[field] = expected[field]
    if current.get("is_active") is not True:
        update["is_active"] = True
    translations = migrate_translations(current.get("translations") or {}, expected["translations"])
    if translations != (current.get("translations") or {}):
        update["translations"] = translations
    skills = merge_skills(current.get("skills") or [], expected["skills"])
    if skills != (current.get("skills") or []):
        update["skills"] = skills
    if "rarity" in current:
        update["rarity"] = DELETE
    return update


def is_final_document(after: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    """Post-write verification of the fields this script is responsible for."""
    return (
        after.get("is_active") is True
        and "rarity" not in after
        and after.get("avatar_url") == expected["avatar_url"]
        and after.get("avatar_urls") == expected["avatar_urls"]
    )


def summarize(update: Mapping[str, Any]) -> List[str]:
    return sorted(update)


def upload_avatar(apply: bool, project_id: str) -> int:
    from database.db_manager import get_storage_bucket

    data = AVATAR_FILE.read_bytes()
    bucket_name = DEFAULT_BUCKET
    expected_url = default_avatar_url(bucket_name)
    print(f"Avatar file {AVATAR_FILE.name}: {len(data)} bytes -> gs://{bucket_name}/{DEFAULT_AVATAR_PATH}")
    print(f"Public URL: {expected_url}")
    if not apply:
        print("Dry run only. Re-run with --apply to upload.")
        return 0
    bucket = get_storage_bucket(bucket_name)
    blob = bucket.blob(DEFAULT_AVATAR_PATH)
    blob.upload_from_string(data, content_type="image/png")
    blob.make_public()
    if blob.public_url != expected_url:
        print(f"WARNING: Storage returned {blob.public_url!r}, catalogue URL is {expected_url!r}.")
        return 1
    print("Uploaded and made public.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--upload-avatar", action="store_true")
    parser.add_argument("--delete-orphans", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import firebase_admin
    from firebase_admin import firestore

    from database.db_manager import init_firebase

    db = init_firebase()
    if firebase_admin.get_app().project_id != args.project_id:
        raise SystemExit("Refusing: initialized Firebase project differs from --project-id.")
    if args.upload_avatar:
        return upload_avatar(args.apply, args.project_id)

    catalogue = {c.id: firestore_character_document(c) for c in load_global_character_catalog()}
    done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if args.resume and PROGRESS_FILE.exists() else set()
    existing = {snap.id: snap for snap in db.collection("characters").stream()}
    updates: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    creates: List[Tuple[str, Dict[str, Any]]] = []
    unchanged = 0
    for doc_id, expected in catalogue.items():
        snap = existing.get(doc_id)
        if snap is None:
            creates.append((doc_id, expected))
        elif doc_id in done:
            unchanged += 1
        else:
            current = snap.to_dict() or {}
            update = plan_character_update(current, expected)
            if update:
                updates.append((doc_id, update, current))
            else:
                unchanged += 1
    orphan_ids = sorted(set(existing) - set(catalogue)) if args.delete_orphans else []
    referenced = set()
    if orphan_ids:
        referenced = {s.id for s in db.collection_group("characters").stream() if s.reference.path.startswith("users/")} & set(orphan_ids)
    fields: Dict[str, int] = {}
    for _id, update, _cur in updates:
        for field in update:
            fields[field] = fields.get(field, 0) + 1
    print(f"Project {args.project_id}: catalogue {len(catalogue)}, to update {len(updates)}, to create {len(creates)}, "
          f"unchanged {unchanged}, orphans {len(orphan_ids)} (referenced: {len(referenced)})")
    print(f"Fields touched: {fields}")
    for doc_id, update, current in updates[:5]:
        print(f"  {doc_id}: {current.get('name')!r} -> {update.get('name', current.get('name'))!r}; fields {summarize(update)}")
    for doc_id, _ in creates:
        print(f"  create {doc_id}")
    for orphan in orphan_ids:
        print(f"  orphan {orphan}: {'REFERENCED, will not be deleted' if orphan in referenced else 'will be deleted'}")
    if not args.apply:
        print("Dry run only. Nothing was written.")
        return 0

    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"finalize_characters_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps({i: s.to_dict() for i, s in existing.items()}, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Backup written: {backup}")
    states: Dict[str, str] = {}
    done_now = set(done)
    operations: List[Tuple[Any, str, Dict[str, Any], str]] = []
    for doc_id, expected in creates:
        operations.append((db.collection("characters").document(doc_id), "create",
                           dict(expected, created_at=firestore.SERVER_TIMESTAMP), doc_id))
    for doc_id, update, _cur in updates:
        payload = {k: (firestore.DELETE_FIELD if v == DELETE else v) for k, v in update.items()}
        operations.append((db.collection("characters").document(doc_id), "update", payload, doc_id))
    for start in range(0, len(operations), 400):
        chunk = operations[start:start + 400]
        try:
            batch = db.batch()
            for ref, kind, payload, _doc in chunk:
                getattr(batch, kind)(ref, payload)
            batch.commit()
            for *_rest, doc_id in chunk:
                states[doc_id] = "written"
                done_now.add(doc_id)
        except Exception as exc:
            for *_rest, doc_id in chunk:
                states[doc_id] = f"skipped: {type(exc).__name__}: {str(exc)[:100]}"
        PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    for orphan in orphan_ids:
        if orphan in referenced:
            states[orphan] = "skipped: referenced under users/*/characters"
        else:
            db.collection("characters").document(orphan).delete()
            states[orphan] = "deleted (backed up)"
    failed = {k: v for k, v in states.items() if v.startswith("skipped")}
    for doc_id, state in failed.items():
        print(f"{doc_id} -> {state}")
    verified = bad = 0
    for doc_id, state in states.items():
        if state != "written":
            continue
        after = db.collection("characters").document(doc_id).get().to_dict() or {}
        if is_final_document(after, catalogue[doc_id]):
            verified += 1
        else:
            bad += 1
            print(f"Verification failed: {doc_id}")
    print(f"Result: {len([s for s in states.values() if s == 'written'])} written, {len(failed)} skipped, "
          f"{verified} verified, {bad} verification failures.")
    return 1 if failed or bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
