"""Safely add legacy character-ability fields to Firestore v1.1.0 questions.

Dry-run by default. With --apply, this only fills absent fields in the exact
knowley-1 project's default Firestore database. Existing values and locale
records outside the current 35-language allowlist are left untouched.

For legacy translations, simplified_question and clarified_options fall back
to the already stored localized question/options. This is deliberately a
no-op compatibility fallback until semantic rewrites are generated. Fields
whose values require question analysis or live player data are explicitly
stored as null rather than fabricated.
"""

from __future__ import annotations

import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping

import firebase_admin
from google.cloud.firestore_v1._helpers import LastUpdateOption

from database.db_manager import init_firebase


EXPECTED_PROJECT = "knowley-1"
QUESTION_VERSION = "1.1.0"
ACTIVE_LOCALES = frozenset(
    {
        "en",
        "de-DE",
        "ar",
        "bn-BD",
        "da-DK",
        "id",
        "fa-IR",
        "nl-NL",
        "fil",
        "fi-FI",
        "fr-FR",
        "hi-IN",
        "ja-JP",
        "ko-KR",
        "pl-PL",
        "hu-HU",
        "ms-MY",
        "no-NO",
        "pt-BR",
        "ro",
        "ru-RU",
        "th",
        "tr-TR",
        "uk",
        "ur",
        "vi",
        "el-GR",
        "cs-CZ",
        "zh-CN",
        "zh-TW",
        "iw-IL",
        "es-419",
        "es-ES",
        "sv-SE",
        "it-IT",
    }
)
ROOT_NULL_FIELDS = (
    "weakest_distractor_index",
    "strongest_distractor_index",
    "logical_order",
    "option_difficulty_order",
    "community_distribution",
)
LOCALE_NULL_FIELDS = (
    "hint",
    "keyword",
    "distractor_trap_note",
    "binary_fallback",
)
LOCALE_COMPAT_FIELDS = ("simplified_question", "clarified_options")
MAX_ESTIMATED_DOCUMENT_BYTES = 900_000


def _scope_difficulty(data: Mapping[str, Any]) -> str | None:
    scope = str(data.get("scope") or "global").strip().lower()
    if scope in {"global", "gl"}:
        value = data.get("difficulty_global") or data.get("difficulty_local")
    elif scope == "local":
        value = data.get("difficulty_local")
    else:
        return None
    normalized = str(value or "").strip().capitalize()
    return normalized if normalized in {"Kolay", "Orta", "Zor"} else None


def _set_path(data: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def _projected_size(data: Mapping[str, Any], updates: Mapping[str, Any]) -> int:
    projected = copy.deepcopy(dict(data))
    for path, value in updates.items():
        _set_path(projected, path, value)
    return len(json.dumps(projected, ensure_ascii=False, default=str).encode("utf-8"))


def _build_updates(data: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    updates: dict[str, Any] = {}
    counters = {
        "root_fields": 0,
        "localized_fields": 0,
        "locale_records": 0,
        "fallback_question_text": 0,
        "fallback_options": 0,
    }

    if "base_points" not in data:
        updates["base_points"] = 100
    if "difficulty" not in data or data.get("difficulty") in (None, ""):
        difficulty = _scope_difficulty(data)
        if difficulty is None:
            raise ValueError("missing difficulty has no valid scope rating")
        updates["difficulty"] = difficulty

    for field in ROOT_NULL_FIELDS:
        if field not in data:
            updates[field] = None

    translations = data.get("translations")
    if not isinstance(translations, Mapping):
        raise ValueError("translations is not a map")

    for locale in sorted(ACTIVE_LOCALES):
        content = translations.get(locale)
        if content is None:
            continue
        if not isinstance(content, Mapping):
            raise ValueError(f"translations.{locale} is not a map")
        question = content.get("question")
        options = content.get("options")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"translations.{locale}.question is missing")
        if not isinstance(options, Mapping) or any(
            not isinstance(options.get(letter), str) or not options[letter].strip()
            for letter in "ABCD"
        ):
            raise ValueError(f"translations.{locale}.options must contain non-empty A-D strings")

        locale_updates = 0
        for field in LOCALE_COMPAT_FIELDS:
            if field in content:
                continue
            if field == "simplified_question":
                value = question
                counters["fallback_question_text"] += 1
            else:
                value = {letter: str(options[letter]) for letter in "ABCD"}
                counters["fallback_options"] += 1
            updates[f"translations.{locale}.{field}"] = value
            locale_updates += 1
        for field in LOCALE_NULL_FIELDS:
            if field not in content:
                updates[f"translations.{locale}.{field}"] = None
                locale_updates += 1
        if locale_updates:
            counters["locale_records"] += 1
            counters["localized_fields"] += locale_updates

    counters["root_fields"] = sum(
        field in updates
        for field in (*ROOT_NULL_FIELDS, "base_points", "difficulty")
    )
    return updates, counters


def _is_v110(data: Mapping[str, Any]) -> bool:
    return str(data.get("dbVersion") or data.get("version") or "") == QUESTION_VERSION


def _verify_document(data: Mapping[str, Any]) -> tuple[bool, str]:
    for field in (*ROOT_NULL_FIELDS, "base_points", "difficulty"):
        if field not in data:
            return False, f"root field {field} absent"
    translations = data.get("translations")
    if not isinstance(translations, Mapping):
        return False, "translations is not a map"
    for locale in ACTIVE_LOCALES.intersection(translations):
        content = translations.get(locale)
        if not isinstance(content, Mapping):
            return False, f"translations.{locale} is not a map"
        for field in (*LOCALE_COMPAT_FIELDS, *LOCALE_NULL_FIELDS):
            if field not in content:
                return False, f"translations.{locale}.{field} absent"
        if not isinstance(content.get("simplified_question"), str) or not content["simplified_question"].strip():
            return False, f"translations.{locale}.simplified_question is unusable"
        clarified = content.get("clarified_options")
        if not isinstance(clarified, Mapping) or any(
            not isinstance(clarified.get(letter), str) or not clarified[letter].strip()
            for letter in "ABCD"
        ):
            return False, f"translations.{locale}.clarified_options is unusable"
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write missing fields; otherwise dry-run only.")
    parser.add_argument("--project-id", help="Required with --apply; must be knowley-1.")
    args = parser.parse_args()
    if args.apply and args.project_id != EXPECTED_PROJECT:
        parser.error(f"--apply requires --project-id {EXPECTED_PROJECT!r}")

    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    database_name = getattr(db, "_database_string", "")
    if project_id != EXPECTED_PROJECT:
        raise SystemExit(f"Refusing operation: connected project is {project_id!r}, expected {EXPECTED_PROJECT!r}.")
    if not database_name.endswith("/databases/(default)"):
        raise SystemExit(f"Refusing operation: unexpected Firestore database {database_name!r}.")

    pending: list[tuple[Any, Mapping[str, Any], dict[str, Any], int]] = []
    counts = {
        "questions": 0,
        "documents_to_update": 0,
        "root_fields": 0,
        "localized_fields": 0,
        "locale_records": 0,
        "fallback_question_text": 0,
        "fallback_options": 0,
    }
    oversized: list[str] = []
    invalid: list[str] = []

    for snapshot in db.collection("questions").stream():
        data = snapshot.to_dict() or {}
        if not _is_v110(data):
            continue
        counts["questions"] += 1
        try:
            updates, item_counts = _build_updates(data)
            if updates:
                estimated_size = _projected_size(data, updates)
                if estimated_size > MAX_ESTIMATED_DOCUMENT_BYTES:
                    oversized.append(f"{snapshot.id}: {estimated_size} bytes")
                    continue
                pending.append((snapshot.reference, snapshot.update_time, updates, estimated_size))
                counts["documents_to_update"] += 1
            for field in (
                "root_fields",
                "localized_fields",
                "locale_records",
                "fallback_question_text",
                "fallback_options",
            ):
                counts[field] += item_counts[field]
        except (TypeError, ValueError) as exc:
            invalid.append(f"{snapshot.id}: {exc}")

    print(
        f"Project: {project_id}; database: {database_name}; v{QUESTION_VERSION} questions: {counts['questions']}"
    )
    print(
        "Missing fields to add: "
        f"{counts['root_fields']} root fields; {counts['localized_fields']} localized fields "
        f"across {counts['locale_records']} active-language records; "
        f"fallback question copies: {counts['fallback_question_text']}; "
        f"fallback option maps: {counts['fallback_options']}"
    )
    print(f"Documents requiring updates: {counts['documents_to_update']}")
    if invalid or oversized:
        print(f"Refusing to write: {len(invalid)} invalid documents; {len(oversized)} near-size-limit documents.")
        for item in (invalid + oversized)[:20]:
            print(f"  {item}")
        return 2
    if not args.apply:
        print("Dry run only. No Firestore data was changed.")
        return 0

    failures: list[tuple[str, str]] = []

    def update_one(item: tuple[Any, Any, dict[str, Any], int]) -> str:
        reference, update_time, original_updates, _estimated_size = item
        updates = dict(original_updates)
        for attempt in range(3):
            try:
                reference.update(updates, option=LastUpdateOption(update_time))
                return reference.id
            except Exception as exc:
                # If a concurrent writer changed this document, re-read and
                # rebuild only still-absent fields before retrying.
                if attempt == 2:
                    raise RuntimeError(f"{type(exc).__name__}: {str(exc)[:180]}") from exc
                fresh = reference.get()
                if not fresh.exists:
                    raise RuntimeError("document disappeared during backfill") from exc
                data = fresh.to_dict() or {}
                if not _is_v110(data):
                    return reference.id
                updates, _ = _build_updates(data)
                if not updates:
                    return reference.id
                update_time = fresh.update_time
        return reference.id

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(update_one, item): item[0].id for item in pending}
        completed = 0
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                future.result()
                completed += 1
                if completed % 100 == 0 or completed == len(pending):
                    print(f"Updated {completed}/{len(pending)} documents.")
            except Exception as exc:
                failures.append((doc_id, str(exc)))
                print(f"Update failed for {doc_id}: {exc}")

    if failures:
        print(f"Stopped with {len(failures)} failed document(s); rerunning is safe and resumes missing fields only.")
        return 3

    verified = 0
    verification_errors: list[str] = []
    for snapshot in db.collection("questions").stream():
        data = snapshot.to_dict() or {}
        if not _is_v110(data):
            continue
        ok, reason = _verify_document(data)
        if not ok:
            verification_errors.append(f"{snapshot.id}: {reason}")
        else:
            verified += 1
    if verification_errors or verified != counts["questions"]:
        for item in verification_errors[:20]:
            print(f"Verification failed: {item}")
        print(f"Post-write verification failed: {verified}/{counts['questions']} documents verified.")
        return 4

    print(
        f"Backfill complete and verified: {verified} v{QUESTION_VERSION} documents. "
        "Existing populated fields and non-allowlisted locales were not changed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
