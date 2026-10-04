"""Safely sync skill review statuses and taxonomy ids without changing other fields."""

from __future__ import annotations

import argparse

import firebase_admin

from core.character_catalog import firestore_character_document, load_global_character_catalog
from database.db_manager import init_firebase


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Update only changed skills arrays.")
    parser.add_argument("--project-id", required=True, help="Expected Firebase project id.")
    args = parser.parse_args()

    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    if project_id != args.project_id:
        raise SystemExit(f"Refusing update: initialized project is {project_id!r}, expected {args.project_id!r}.")

    changes = []
    for character in load_global_character_catalog():
        ref = db.collection("characters").document(character.id)
        snapshot = ref.get()
        if not snapshot.exists:
            raise SystemExit(f"Refusing update: character document {character.id!r} is missing.")

        current = snapshot.to_dict() or {}
        current.pop("created_at", None)
        expected = firestore_character_document(character)
        allowed_fields = {"skills", "category_id", "sub_category_id"}
        current_other = {key: value for key, value in current.items() if key not in allowed_fields}
        expected_other = {key: value for key, value in expected.items() if key not in allowed_fields}
        if current_other != expected_other:
            raise SystemExit(f"Refusing update: non-skill fields changed for {character.id!r}.")

        old_skills = current.get("skills")
        new_skills = expected["skills"]
        if not isinstance(old_skills, list) or len(old_skills) != len(new_skills):
            raise SystemExit(f"Refusing update: skill array shape changed for {character.id!r}.")
        for old, new in zip(old_skills, new_skills):
            old_without_status = {key: value for key, value in old.items() if key != "implementation_status"}
            new_without_status = {key: value for key, value in new.items() if key != "implementation_status"}
            if old_without_status != new_without_status:
                raise SystemExit(f"Refusing update: skill parameters changed for {character.id!r}.")
        status_changed = any(old.get("implementation_status") != new.get("implementation_status") for old, new in zip(old_skills, new_skills))
        taxonomy_changed = any(current.get(field) != expected.get(field) for field in ("category_id", "sub_category_id"))
        if status_changed or taxonomy_changed:
            changes.append((ref, new_skills, character.category_id, character.sub_category_id))

    print(f"Project: {project_id}; documents with catalog metadata changes: {len(changes)}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to update only each affected character's skills and taxonomy ids.")
        return 0

    batch = db.batch()
    for ref, skills, category_id, sub_category_id in changes:
        batch.update(ref, {
            "skills": skills,
            "category_id": category_id,
            "sub_category_id": sub_category_id,
        })
    if changes:
        batch.commit()
    print(f"Updated catalog metadata in {len(changes)} global character documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
