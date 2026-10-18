"""Canonicalize v1.1.0 question display names from their sheet region codes.

Dry-run by default. With --apply, only target_country, target_country_code,
and countries are eligible for correction on exact v1.1.0 documents.
Global target_country values are contextual focus labels and are preserved.
"""

from __future__ import annotations

import argparse
from typing import Any

import firebase_admin
from google.cloud.firestore_v1._helpers import LastUpdateOption

from core.sheet_algorithm import get_sheet_algorithm_repository
from database.db_manager import init_firebase


EXPECTED_PROJECT = "knowley-1"
EXPECTED_DATABASE = "/databases/(default)"
VERSION = "1.1.0"
FIELDS = ["dbVersion", "version", "scope", "target_country", "target_country_code", "countries"]


def _version(data: dict[str, Any]) -> str:
    return str(data.get("dbVersion") or data.get("version") or "").strip()


def _updates_for(data: dict[str, Any], names_by_code: dict[str, str]) -> tuple[dict[str, Any], str | None]:
    scope = str(data.get("scope") or "").strip().casefold()
    code = str(data.get("target_country_code") or "").strip().upper()
    countries = data.get("countries")

    if scope in {"global", "gl"}:
        updates: dict[str, Any] = {}
        if code != "GL":
            updates["target_country_code"] = "GL"
        if countries != ["GL"]:
            updates["countries"] = ["GL"]
        return updates, None

    if scope != "local":
        return {}, f"unsupported scope {scope or '(missing)'}"

    if code not in names_by_code:
        return {}, f"country code {code or '(missing)'} is not in the sheet region list"

    updates = {}
    expected_name = names_by_code[code]
    if str(data.get("target_country") or "").strip() != expected_name:
        updates["target_country"] = expected_name
    if str(data.get("target_country_code") or "").strip() != code:
        updates["target_country_code"] = code
    if countries != [code]:
        updates["countries"] = [code]
    return updates, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write only the missing/noncanonical country fields.")
    parser.add_argument("--project-id", help="Required with --apply; must be knowley-1.")
    args = parser.parse_args()
    if args.apply and args.project_id != EXPECTED_PROJECT:
        parser.error(f"--apply requires --project-id {EXPECTED_PROJECT!r}")

    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    database_name = getattr(db, "_database_string", "")
    if project_id != EXPECTED_PROJECT:
        raise SystemExit(f"Refusing operation: connected project is {project_id!r}, expected {EXPECTED_PROJECT!r}.")
    if not database_name.endswith(EXPECTED_DATABASE):
        raise SystemExit(f"Refusing operation: unexpected Firestore database {database_name!r}.")

    names_by_code = {
        str(row.get("code") or "").strip().upper(): str(row.get("name") or "").strip()
        for row in get_sheet_algorithm_repository().countries()
        if row.get("code") and row.get("name")
    }
    planned: list[tuple[Any, dict[str, Any]]] = []
    unresolved: list[tuple[str, str]] = []
    totals = {"v110_documents": 0, "documents_to_update": 0, "global": 0, "local": 0}

    query = db.collection("questions").select(FIELDS)
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        if _version(data) != VERSION:
            continue
        totals["v110_documents"] += 1
        scope = str(data.get("scope") or "").strip().casefold()
        totals["global" if scope in {"global", "gl"} else "local"] += 1
        updates, issue = _updates_for(data, names_by_code)
        if issue:
            unresolved.append((snapshot.id, issue))
            continue
        if updates:
            planned.append((snapshot, updates))

    totals["documents_to_update"] = len(planned)
    print(f"Project: {project_id}; database: {database_name}")
    print(f"v1.1.0 documents: {totals['v110_documents']}; global: {totals['global']}; local: {totals['local']}")
    print(f"Documents to update: {len(planned)}; unresolved: {len(unresolved)}")
    for snapshot, updates in planned:
        print(
            f"{snapshot.id}: target_country={snapshot.get('target_country')!r} -> "
            f"{updates.get('target_country', snapshot.get('target_country'))!r}; "
            f"fields={','.join(sorted(updates))}"
        )
    for document_id, reason in unresolved[:30]:
        print(f"UNRESOLVED {document_id}: {reason}")

    if not args.apply:
        print("Dry run only; no Firestore data was changed.")
        return 1 if unresolved else 0

    for snapshot, updates in planned:
        if snapshot.update_time is None:
            raise SystemExit(f"Refusing write: missing update_time for {snapshot.id}.")
        snapshot.reference.update(updates, option=LastUpdateOption(snapshot.update_time))

    failures = []
    for snapshot, expected_updates in planned:
        actual = snapshot.reference.get().to_dict() or {}
        if any(actual.get(field) != value for field, value in expected_updates.items()):
            failures.append(snapshot.id)
    if failures:
        raise SystemExit(f"Post-write verification failed for: {', '.join(failures[:20])}")

    print(f"Updated and verified {len(planned)} v1.1.0 question(s).")
    print("Only country identity fields were written; question content and other metadata were preserved.")
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
