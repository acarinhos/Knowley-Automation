"""Add requested regional locale keys by reusing valid legacy translations.

Only updates ``translations``, ``supported_languages``,
``target_country_code``, and ``countries`` on version 1.1.0 questions.
Existing translation leaves and unrelated document fields are preserved.
"""

from __future__ import annotations

import copy
import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping

from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import backfill_v110_translations as backfill
from core.sheet_algorithm import get_sheet_algorithm_repository


def _decode(value: Mapping[str, Any]) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "booleanValue" in value:
        return value["booleanValue"]
    if "nullValue" in value:
        return None
    if "mapValue" in value:
        return {
            key: _decode(child)
            for key, child in value["mapValue"].get("fields", {}).items()
        }
    if "arrayValue" in value:
        return [_decode(child) for child in value["arrayValue"].get("values", [])]
    return None


def _string(value: Any) -> Dict[str, str]:
    return {"stringValue": str(value)}


def _array(values: list[str]) -> Dict[str, Any]:
    return {"arrayValue": {"values": [_string(value) for value in values]}}


def _locale_fields(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    map_value = value.get("mapValue")
    if not isinstance(map_value, Mapping):
        return {}
    fields = map_value.get("fields")
    return fields if isinstance(fields, dict) else {}


def _merge_missing_locale_leaves(
    target_value: Any, source_value: Mapping[str, Any]
) -> tuple[Dict[str, Any], int]:
    """Fill blank known fields without replacing any existing non-empty value."""
    if not isinstance(target_value, Mapping) or not target_value.get("mapValue"):
        return copy.deepcopy(dict(source_value)), 1

    target = copy.deepcopy(dict(target_value))
    target_fields = _locale_fields(target)
    source_fields = _locale_fields(source_value)
    added = 0
    for key in ("question", "explanation", "filter_tag"):
        target_text = _decode(target_fields[key]) if key in target_fields else ""
        source_text = _decode(source_fields[key]) if key in source_fields else ""
        if not str(target_text or "").strip() and str(source_text or "").strip():
            target_fields[key] = copy.deepcopy(source_fields[key])
            added += 1

    target_options_value = target_fields.get("options")
    if not isinstance(target_options_value, Mapping) or not target_options_value.get("mapValue"):
        target_options_value = {"mapValue": {"fields": {}}}
        target_fields["options"] = target_options_value
    target_options = _locale_fields(target_options_value)
    source_options = _locale_fields(source_fields.get("options"))
    for letter in "ABCD":
        target_text = _decode(target_options[letter]) if letter in target_options else ""
        source_text = _decode(source_options[letter]) if letter in source_options else ""
        if not str(target_text or "").strip() and str(source_text or "").strip():
            target_options[letter] = copy.deepcopy(source_options[letter])
            added += 1
    return target, added


def _read_questions(session: AuthorizedSession, project_id: str) -> list[Dict[str, Any]]:
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}"
        "/databases/(default)/documents:runQuery"
    )
    query = {
        "structuredQuery": {
            "from": [{"collectionId": backfill.COLLECTION}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "version"},
                    "op": "EQUAL",
                    "value": {"stringValue": backfill.VERSION},
                }
            },
        }
    }
    response = session.post(url, json=query, timeout=(15, 120))
    response.raise_for_status()
    return [item["document"] for item in response.json() if item.get("document")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without writing")
    args = parser.parse_args()
    load_dotenv(ROOT_DIR / ".env")
    credential_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    if not credential_path or not Path(credential_path).is_file():
        raise FileNotFoundError("FIREBASE_CREDENTIALS_PATH does not point to a service-account file")
    credentials = service_account.Credentials.from_service_account_file(
        credential_path,
        scopes=[backfill.GOOGLE_CLOUD_SCOPE],
    )
    session = AuthorizedSession(credentials)
    project_id = credentials.project_id
    database_path = f"projects/{project_id}/databases/(default)"
    commit_url = f"https://firestore.googleapis.com/v1/{database_path}/documents:commit"
    repository = get_sheet_algorithm_repository()
    documents = _read_questions(session, project_id)

    writes: list[Dict[str, Any]] = []
    alias_documents = 0
    alias_locales = 0
    alias_leaves = 0
    metadata_documents = 0
    unresolved = []

    for document in documents:
        doc_id = document["name"].rsplit("/", 1)[-1]
        fields = document.get("fields", {})
        data = {key: _decode(value) for key, value in fields.items()}
        snapshot = SimpleNamespace(id=doc_id, to_dict=lambda data=data: data)
        try:
            plan = backfill._plan(snapshot, repository)
        except Exception as exc:
            unresolved.append({"id": doc_id, "reason": str(exc)})
            continue

        update_fields: Dict[str, Any] = {}
        update_mask = []
        translations_value = fields.get("translations")
        translations = (
            copy.deepcopy(dict(translations_value))
            if isinstance(translations_value, Mapping)
            else {"mapValue": {"fields": {}}}
        )
        locale_fields = _locale_fields(translations)
        doc_alias_locales = 0
        doc_alias_leaves = 0
        for locale, content in plan["aliases"].items():
            legacy = backfill.LEGACY_LOCALE_SOURCES.get(locale)
            source_value = locale_fields.get(legacy) if legacy else None
            if not isinstance(source_value, Mapping):
                continue
            merged, added = _merge_missing_locale_leaves(
                locale_fields.get(locale), source_value
            )
            if added:
                locale_fields[locale] = merged
                doc_alias_locales += 1
                doc_alias_leaves += added

        if doc_alias_locales:
            update_fields["translations"] = translations
            update_mask.append("translations")
            alias_documents += 1
            alias_locales += doc_alias_locales
            alias_leaves += doc_alias_leaves

        available_languages = [
            language for language in plan["targets"] if language in plan["translations"]
        ]
        if list(data.get("supported_languages") or []) != available_languages:
            update_fields["supported_languages"] = _array(available_languages)
            update_mask.append("supported_languages")
        code = plan["country_code"]
        if str(data.get("target_country_code") or "").strip().upper() != code:
            update_fields["target_country_code"] = _string(code)
            update_mask.append("target_country_code")
        if list(data.get("countries") or []) != [code]:
            update_fields["countries"] = _array([code])
            update_mask.append("countries")

        if len(update_mask) > (1 if doc_alias_locales else 0):
            metadata_documents += 1
        if not update_fields:
            continue
        writes.append(
            {
                "update": {
                    "name": document["name"],
                    "fields": update_fields,
                },
                "updateMask": {"fieldPaths": update_mask},
                "currentDocument": {"updateTime": document["updateTime"]},
            }
        )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "matched_version_documents": len(documents),
                    "documents_to_write": len(writes),
                    "documents_with_locale_aliases": alias_documents,
                    "regional_locale_keys_to_add": alias_locales,
                    "translation_leaves_to_add": alias_leaves,
                    "metadata_only_documents": metadata_documents,
                    "unresolved": unresolved[:20],
                    "write_scope": ["translations", "supported_languages", "target_country_code", "countries"],
                },
                ensure_ascii=False,
            )
        )
        return 1 if unresolved else 0

    committed = 0
    chunk: list[Dict[str, Any]] = []
    chunk_size = 0
    for write in writes:
        encoded_size = len(json.dumps(write, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if chunk and (len(chunk) >= 75 or chunk_size + encoded_size > 2_000_000):
            response = session.post(commit_url, json={"writes": chunk}, timeout=(15, 120))
            response.raise_for_status()
            committed += len(response.json().get("writeResults", []))
            chunk = []
            chunk_size = 0
        chunk.append(write)
        chunk_size += encoded_size
    if chunk:
        response = session.post(commit_url, json={"writes": chunk}, timeout=(15, 120))
        response.raise_for_status()
        committed += len(response.json().get("writeResults", []))

    print(
        json.dumps(
            {
                "matched_version_documents": len(documents),
                "documents_written": committed,
                "documents_with_locale_aliases": alias_documents,
                "regional_locale_keys_added": alias_locales,
                "translation_leaves_added": alias_leaves,
                "metadata_only_documents": metadata_documents,
                "unresolved": unresolved[:20],
                "write_scope": ["translations", "supported_languages", "target_country_code", "countries"],
                "precondition": "all writes required the original document updateTime",
            },
            ensure_ascii=False,
        )
    )
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
