"""Fill missing difficulty ratings on v1.1.0 Firestore questions.

Dry-run by default. Applying only fills missing global-scope difficulty fields
from that same document's already-validated local rating; existing values and
unrelated question fields are never overwritten.
"""

from __future__ import annotations

import argparse
from typing import Any

import firebase_admin
from database.db_manager import init_firebase


QUESTION_VERSION = "1.1.0"
EXPECTED_PROJECT = "knowley-1"
RANGES = {"Kolay": (1, 4), "Orta": (5, 8), "Zor": (9, 10)}


def _valid_rating(label: Any, score: Any) -> bool:
    if label not in RANGES or not isinstance(score, int) or isinstance(score, bool):
        return False
    low, high = RANGES[label]
    return low <= score <= high


def _nested_matches(profile: Any, meta: Any, label: Any, score: Any) -> bool:
    if not isinstance(profile, dict) or not isinstance(meta, dict):
        return False
    profile_label = profile.get("label", profile.get("level"))
    meta_label = meta.get("level", meta.get("label"))
    return profile_label == label and profile.get("score") == score and meta_label == label and meta.get("score") == score


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write missing fields; otherwise dry-run.")
    parser.add_argument("--project-id", help="Required with --apply; must match the target Firebase project.")
    args = parser.parse_args()
    if args.apply and args.project_id != EXPECTED_PROJECT:
        parser.error(f"--apply requires --project-id {EXPECTED_PROJECT!r}")

    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    if project_id != EXPECTED_PROJECT:
        raise SystemExit(f"Refusing operation: connected project is {project_id!r}, expected {EXPECTED_PROJECT!r}.")

    updates: list[tuple[Any, dict[str, Any]]] = []
    counts = {"questions": 0, "local_valid": 0, "global_valid": 0, "global_missing": 0}
    invalid: list[str] = []

    for snapshot in db.collection("questions").stream():
        data = snapshot.to_dict() or {}
        if str(data.get("dbVersion") or data.get("version") or "") != QUESTION_VERSION:
            continue
        counts["questions"] += 1
        scope = str(data.get("scope") or "global").strip().lower()

        local_label = data.get("difficulty_local")
        local_score = data.get("difficulty_local_score")
        if not _valid_rating(local_label, local_score):
            invalid.append(f"{snapshot.id}: invalid local rating")
            continue

        if scope == "local":
            profile = (data.get("difficulty_profile") or {}).get("local")
            meta = (data.get("difficulty_meta") or {}).get("local")
            if not _nested_matches(profile, meta, local_label, local_score):
                invalid.append(f"{snapshot.id}: local nested rating mismatch")
                continue
            counts["local_valid"] += 1
            continue
        if scope not in {"global", "gl"}:
            invalid.append(f"{snapshot.id}: unsupported scope {scope!r}")
            continue

        global_label = data.get("difficulty_global")
        global_score = data.get("difficulty_global_score")
        if global_label is None and global_score is None:
            # Legacy global documents sometimes only carry their general
            # difficulty rating. Use it as the fallback, without changing the
            # existing local rating or root difficulty field.
            global_label, global_score = local_label, local_score
            profile = (data.get("difficulty_profile") or {}).get("global")
            meta = (data.get("difficulty_meta") or {}).get("global")
            if profile not in (None, {}) or meta not in (None, {}):
                invalid.append(f"{snapshot.id}: missing root global rating has non-empty nested rating")
                continue
            updates.append(
                (
                    snapshot.reference,
                    {
                        "difficulty_global": global_label,
                        "difficulty_global_score": global_score,
                        "difficulty_profile.global": {"label": global_label, "score": global_score},
                        "difficulty_meta.global": {"level": global_label, "score": global_score},
                    },
                )
            )
            counts["global_missing"] += 1
        elif not _valid_rating(global_label, global_score):
            invalid.append(f"{snapshot.id}: invalid or partial global rating")
            continue
        else:
            profile = (data.get("difficulty_profile") or {}).get("global")
            meta = (data.get("difficulty_meta") or {}).get("global")
            if not _nested_matches(profile, meta, global_label, global_score):
                invalid.append(f"{snapshot.id}: global nested rating mismatch")
                continue
            counts["global_valid"] += 1

    if invalid:
        raise SystemExit("Refusing partial update; invalid records found:\n" + "\n".join(invalid[:20]))

    print(
        f"Project: {project_id}; v1.1.0: {counts['questions']}; "
        f"valid local ratings: {counts['local_valid']}; "
        f"valid existing global ratings: {counts['global_valid']}; "
        f"missing global ratings to fill: {counts['global_missing']}"
    )
    if not args.apply:
        print("Dry run only. No Firestore data was changed.")
        return 0

    for start in range(0, len(updates), 400):
        batch = db.batch()
        for reference, fields in updates[start : start + 400]:
            batch.update(reference, fields)
        batch.commit()

    # Re-scan the complete v1.1.0 inventory and verify scope ratings.
    verified = {"questions": 0, "valid": 0}
    for snapshot in db.collection("questions").stream():
        data = snapshot.to_dict() or {}
        if str(data.get("dbVersion") or data.get("version") or "") != QUESTION_VERSION:
            continue
        verified["questions"] += 1
        scope = str(data.get("scope") or "global").strip().lower()
        label_key, score_key = (
            ("difficulty_local", "difficulty_local_score")
            if scope == "local"
            else ("difficulty_global", "difficulty_global_score")
        )
        label, score = data.get(label_key), data.get(score_key)
        profile = (data.get("difficulty_profile") or {}).get("local" if scope == "local" else "global")
        meta = (data.get("difficulty_meta") or {}).get("local" if scope == "local" else "global")
        if (
            _valid_rating(label, score)
            and (profile or {}).get("label", (profile or {}).get("level")) == label
            and (profile or {}).get("score") == score
            and (meta or {}).get("level", (meta or {}).get("label")) == label
            and (meta or {}).get("score") == score
        ):
            verified["valid"] += 1
        else:
            raise SystemExit(f"Post-write verification failed for {snapshot.id!r}.")

    if verified["questions"] != counts["questions"] or verified["valid"] != counts["questions"]:
        raise SystemExit(f"Post-write inventory verification failed: {verified!r}")
    print(f"Updated and verified {len(updates)} missing global rating(s); all {verified['valid']} v1.1.0 ratings are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
