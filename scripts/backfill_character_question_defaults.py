"""Add safe character-runtime defaults to legacy 1.1.0 question documents.

Dry-run by default. Applying only fills absent ``base_points`` and ``difficulty``
fields; it never overwrites existing question values.
"""

from __future__ import annotations

import argparse
from typing import Any

import firebase_admin
from database.db_manager import init_firebase


QUESTION_VERSION = "1.1.0"
VALID_DIFFICULTIES = {"Kolay", "Orta", "Zor"}


def _scope_difficulty(data: dict[str, Any]) -> str | None:
    scope = str(data.get("scope") or "global").strip().lower()
    if scope in {"global", "gl"}:
        value = data.get("difficulty_global") or data.get("difficulty_local")
    elif scope == "local":
        value = data.get("difficulty_local")
    else:
        return None
    normalized = str(value or "").strip().capitalize()
    return normalized if normalized in VALID_DIFFICULTIES else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write only missing default fields.")
    parser.add_argument("--project-id", help="Required with --apply; must match initialized Firebase project.")
    args = parser.parse_args()
    if args.apply and not args.project_id:
        parser.error("--project-id is required with --apply")

    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    if args.project_id and project_id != args.project_id:
        raise SystemExit(f"Refusing update: initialized project is {project_id!r}, expected {args.project_id!r}.")

    pending: list[tuple[Any, dict[str, Any]]] = []
    counts = {"questions": 0, "base_points_to_add": 0, "difficulty_to_add": 0, "invalid_difficulty": 0}
    fallback_to_local = 0
    for snapshot in db.collection("questions").stream():
        data = snapshot.to_dict() or {}
        if str(data.get("dbVersion") or data.get("version") or "") != QUESTION_VERSION:
            continue
        counts["questions"] += 1
        updates: dict[str, Any] = {}
        if "base_points" not in data:
            updates["base_points"] = 100
            counts["base_points_to_add"] += 1
        if "difficulty" not in data or data.get("difficulty") in (None, ""):
            value = _scope_difficulty(data)
            if value is None:
                counts["invalid_difficulty"] += 1
            else:
                updates["difficulty"] = value
                counts["difficulty_to_add"] += 1
                if str(data.get("scope") or "global").strip().lower() in {"global", "gl"} and not data.get("difficulty_global"):
                    fallback_to_local += 1
        if updates:
            pending.append((snapshot.reference, updates))

    if counts["invalid_difficulty"]:
        raise SystemExit(
            f"Refusing partial update: {counts['invalid_difficulty']} question(s) have no supported scope difficulty."
        )

    print(
        f"Project: {project_id}; 1.1.0 questions: {counts['questions']}; "
        f"base_points to add: {counts['base_points_to_add']}; "
        f"difficulty to add: {counts['difficulty_to_add']}; "
        f"global questions using local fallback: {fallback_to_local}"
    )
    if not args.apply:
        print("Dry run only. No Firestore data was changed.")
        return 0

    for start in range(0, len(pending), 450):
        batch = db.batch()
        for ref, updates in pending[start : start + 450]:
            batch.update(ref, updates)
        batch.commit()

    verified = 0
    for ref, expected in pending:
        actual = ref.get().to_dict() or {}
        if any(actual.get(key) != value for key, value in expected.items()):
            raise SystemExit(f"Post-write verification failed for {ref.id!r}.")
        verified += 1
    print(f"Added missing defaults and verified {verified} question document(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
