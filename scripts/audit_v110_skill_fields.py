"""Read-only coverage audit of skill-support fields on Firestore 1.1.0 questions.

Run: python -m scripts.audit_v110_skill_fields   (no writes; counts only)
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ROOT_FIELDS = ("correct_answer", "base_points", "difficulty", "duration_seconds", "duration_global", "duration_local",
               "weakest_distractor_index", "strongest_distractor_index", "option_difficulty_order", "community_distribution")
LOCALE_FIELDS = ("question", "options", "explanation", "hint", "keyword", "distractor_trap_note",
                 "binary_fallback", "simplified_question", "clarified_options")


def present(value) -> bool:
    return value not in (None, "", [], {})


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locales", default="", help="Comma-separated locales to count (default: all), e.g. en")
    only = {x.strip() for x in parser.parse_args().locales.split(",") if x.strip()}
    load_dotenv(ROOT / ".env")
    from database.db_manager import init_firebase  # noqa: PLC0415
    from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: PLC0415

    db = init_firebase()
    docs = {}
    for field in ("dbVersion", "version"):
        for snap in db.collection("questions").where(filter=FieldFilter(field, "==", "1.1.0")).stream():
            docs[snap.id] = snap.to_dict()
    root_missing, locale_missing, locale_total = Counter(), Counter(), Counter()
    for data in docs.values():
        for field in ROOT_FIELDS:
            if field == "community_distribution":
                continue  # nullable by contract
            if field.startswith("duration_") and field != "duration_seconds":
                continue  # one of the duration fields is enough
            if not present(data.get(field)) and data.get(field) != 0:
                root_missing[field] += 1
        for loc, tr in (data.get("translations") or {}).items():
            if only and loc not in only:
                continue
            for field in LOCALE_FIELDS:
                locale_total[field] += 1
                if not present((tr or {}).get(field)):
                    locale_missing[field] += 1
    total = len(docs)
    print(f"1.1.0 documents: {total}")
    for field in ROOT_FIELDS:
        print(f"root {field}: missing {root_missing[field]} ({root_missing[field] / max(total, 1):.1%})")
    for field in LOCALE_FIELDS:
        n = locale_total[field]
        print(f"locale {field}: missing {locale_missing[field]}/{n} ({locale_missing[field] / max(n, 1):.1%})")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
