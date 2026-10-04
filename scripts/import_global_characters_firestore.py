"""Add the global character catalogue to Firestore; dry-run unless --apply is passed."""

from __future__ import annotations

import argparse
import firebase_admin
from firebase_admin import firestore

from core.character_catalog import firestore_character_document, load_global_character_catalog
from database.db_manager import init_firebase


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the additive catalogue documents.")
    parser.add_argument("--project-id", help="Refuse to write if the initialized Firebase project differs.")
    args = parser.parse_args()

    characters = load_global_character_catalog()
    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    if args.project_id and project_id != args.project_id:
        raise SystemExit(f"Refusing write: initialized project is {project_id!r}, expected {args.project_id!r}.")

    collisions = sorted(
        character.id
        for character in characters
        if db.collection("characters").document(character.id).get().exists
    )
    if collisions and args.apply:
        raise SystemExit(
            f"Refusing to overwrite {len(collisions)} existing character document(s); inspect collisions first."
        )

    print(f"Project: {project_id}; global catalogue: {len(characters)}; existing id collisions: {len(collisions)}")
    if not args.apply:
        print("Dry run only. Re-run with --apply after reviewing the catalogue.")
        return 0

    batch = db.batch()
    writes = 0
    for character in characters:
        payload = firestore_character_document(character)
        payload["created_at"] = firestore.SERVER_TIMESTAMP
        ref = db.collection("characters").document(character.id)
        batch.set(ref, payload, merge=False)
        writes += 1
        if writes == 450:
            batch.commit()
            batch = db.batch()
            writes = 0
    if writes:
        batch.commit()
    print(f"Imported {len(characters)} global character documents (inactive pending game-engine integration).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
