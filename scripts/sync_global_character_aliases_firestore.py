"""Sync alias-renamed display text of the global catalogue to Firestore `characters`.

Dry-run by default. With --apply it (1) writes a local JSON backup of the whole collection,
(2) adds catalogue characters that are missing (is_active=false) and (3) updates ONLY
name, title, role, translations.<locale>.{name,title,role} and the text fields of existing skills
(name, source_description, source_sheet_text, manual_review source_text). Nothing is deleted;
is_active, created_at and every other field are untouched.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import firestore

from core.character_catalog import firestore_character_document, load_global_character_catalog
from database.db_manager import init_firebase

ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT_FIELDS = ("name", "source_description", "source_sheet_text")


def planned_update(current: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Return only the changed allowed fields, built from the current document."""
    update: dict[str, Any] = {key: expected[key] for key in ("name", "title", "role") if current.get(key) != expected[key]}
    translations = copy.deepcopy(current.get("translations") or {})
    for locale, values in expected["translations"].items():
        target = translations.setdefault(locale, {})
        for field in ("name", "title", "role"):
            target[field] = values[field]
    if translations != (current.get("translations") or {}):
        update["translations"] = translations
    skills = copy.deepcopy(current.get("skills") or [])
    if len(skills) != len(expected["skills"]):
        raise SystemExit("Skill array shape differs; refusing to touch skills.")
    for old, new in zip(skills, expected["skills"]):
        if old.get("skill_id") != new["skill_id"]:
            raise SystemExit(f"Skill id mismatch on {new['skill_id']}.")
        for field in SKILL_TEXT_FIELDS:
            old[field] = new[field]
        for old_effect, new_effect in zip(old.get("effects") or [], new["effects"]):
            if old_effect.get("type") == "manual_review" and "source_text" in (new_effect.get("params") or {}):
                old_effect.setdefault("params", {})["source_text"] = new_effect["params"]["source_text"]
    if skills != (current.get("skills") or []):
        update["skills"] = skills
    return update


def backup(db: Any) -> Path:
    folder = ROOT / "local_backups"
    folder.mkdir(exist_ok=True)
    path = folder / f"characters_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    docs = {snap.id: snap.to_dict() for snap in db.collection("characters").stream()}
    path.write_text(json.dumps(docs, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id", required=True, help="Expected Firebase project id.")
    args = parser.parse_args()

    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    if project_id != args.project_id:
        raise SystemExit(f"Refusing: initialized project is {project_id!r}, expected {args.project_id!r}.")

    missing, updates = [], []
    for character in load_global_character_catalog():
        expected = firestore_character_document(character)
        ref = db.collection("characters").document(character.id)
        snapshot = ref.get()
        if not snapshot.exists:
            missing.append((ref, expected))
            continue
        current = snapshot.to_dict() or {}
        if current.get("region_code") != "GL" or current.get("character_type") != "global":
            raise SystemExit(f"{character.id} is not a global character document.")
        update = planned_update(current, expected)
        if update:
            updates.append((ref, update, current))

    fields = {}
    for _ref, update, _cur in updates:
        for key in update:
            fields[key] = fields.get(key, 0) + 1
    print(f"Project: {project_id}; missing docs to add: {len(missing)}; docs to update: {len(updates)}; fields: {fields}")
    for ref, update, current in updates[:5]:
        print(f"  {ref.id}: name {current.get('name')!r} -> {update.get('name')!r}")
    if not args.apply:
        print("Dry run only. No Firestore data was changed.")
        return 0

    print(f"Backup written: {backup(db)}")
    for ref, expected in missing:
        payload = dict(expected, is_active=False, created_at=firestore.SERVER_TIMESTAMP)
        ref.create(payload)  # create() fails if the document already exists
    for ref, update, _cur in updates:
        ref.update(update)

    bad = 0
    for ref, update, current in updates:
        after = ref.get().to_dict() or {}
        if any(after.get(k) != v for k, v in update.items()) or after.get("is_active") != current.get("is_active") \
                or after.get("created_at") != current.get("created_at"):
            bad += 1
            print(f"Verification failed: {ref.id}")
    print(f"Added {len(missing)}, updated {len(updates)}, verification failures {bad}.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
