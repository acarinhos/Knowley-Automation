"""Safely sync Sheet-derived global character skills to Firestore.

Dry-run by default. Applying updates changes only characters/{id}.skills;
translations, activation state, metadata, and every other root field are kept.
"""

from __future__ import annotations

import argparse
from typing import Any

import firebase_admin
from firebase_admin import firestore

from core.character_catalog import firestore_character_document, load_global_character_catalog
from database.db_manager import init_firebase


def _same(left: Any, right: Any) -> bool:
    """Compare Firestore-returned maps/lists to the local catalog payload."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changed skills arrays.")
    parser.add_argument("--project-id", help="Required with --apply; must match the initialized Firebase project.")
    args = parser.parse_args()

    if args.apply and not args.project_id:
        parser.error("--project-id is required with --apply")

    characters = load_global_character_catalog()
    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    if args.project_id and project_id != args.project_id:
        raise SystemExit(f"Refusing sync: initialized project is {project_id!r}, expected {args.project_id!r}.")

    pending: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    unchanged = 0
    for character in characters:
        ref = db.collection("characters").document(character.id)
        snapshot = ref.get()
        if not snapshot.exists:
            raise SystemExit(f"Refusing partial sync: character document {character.id!r} is missing.")

        current = snapshot.to_dict() or {}
        if current.get("region_code") != "GL" or current.get("character_type") != "global":
            raise SystemExit(f"Refusing sync: {character.id!r} is not a global character document.")

        expected = firestore_character_document(character)
        next_skills = expected["skills"]
        if _same(current.get("skills"), next_skills):
            unchanged += 1
            continue
        pending.append((ref, next_skills, current))

    print(
        f"Project: {project_id}; verified global character docs: {len(characters)}; "
        f"skills to update: {len(pending)}; already current: {unchanged}"
    )
    if not args.apply:
        print("Dry run only. No Firestore data was changed.")
        return 0
    if not pending:
        print("All character skills are already current.")
        return 0

    for start in range(0, len(pending), 450):
        batch = db.batch()
        chunk = pending[start : start + 450]
        for ref, skills, _current in chunk:
            batch.update(ref, {"skills": skills})
        batch.commit()

    verified = 0
    for ref, expected_skills, previous in pending:
        updated = ref.get().to_dict() or {}
        if not _same(updated.get("skills"), expected_skills):
            raise SystemExit(f"Post-write verification failed for {ref.id!r}.")
        if updated.get("translations") != previous.get("translations"):
            raise SystemExit(f"Translation preservation check failed for {ref.id!r}.")
        if updated.get("is_active") != previous.get("is_active"):
            raise SystemExit(f"Activation-state preservation check failed for {ref.id!r}.")
        verified += 1

    print(f"Updated and verified skills on {verified} global character documents.")
    print("Only the skills field was written; translations and activation states were verified unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
