"""Sync 1.1.0 visual-question payload URLs with their verified top-level URLs.

Default mode only reports the number of eligible documents. Run with --apply
only after the Firestore update has been explicitly approved.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database.db_manager import init_firebase  # noqa: E402
from firebase_admin import firestore  # noqa: E402


def repair(*, apply: bool = False) -> dict[str, int]:
    db = init_firebase()
    stats = {"visual": 0, "eligible": 0, "updated": 0, "skipped": 0}
    for snapshot in db.collection("questions").stream():
        data = snapshot.to_dict() or {}
        version = str(data.get("dbVersion") or data.get("version") or "")
        if version != "1.1.0" or data.get("is_visual_question") is not True:
            continue
        stats["visual"] += 1
        url = str(data.get("question_image_url") or "").strip()
        payload = data.get("payload")
        if not url.startswith("https://") or not isinstance(payload, dict):
            stats["skipped"] += 1
            continue
        if payload.get("question_image_url") == url and payload.get("question_image_search_query") is None:
            continue
        stats["eligible"] += 1
        if apply:
            snapshot.reference.update(
                {
                    "payload.question_image_url": url,
                    "payload.question_image_search_query": None,
                },
                option=firestore.LastUpdateOption(snapshot.update_time),
            )
            stats["updated"] += 1
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write only eligible payload image fields")
    args = parser.parse_args()
    print(repair(apply=args.apply))
