"""Export and safely apply Codex-generated translations for Firebase v1.1.0.

This bridge never calls a translation provider. Exported content is supplied to
the current Codex agent, and generated JSON is applied only to missing
translation leaves plus explicitly required country/language-code metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from google.cloud import firestore
from google.cloud.firestore_v1.services.firestore.client import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports import FirestoreRestTransport
from google.oauth2 import service_account
from dotenv import load_dotenv

import backfill_v110_translations as backfill
from core.sheet_algorithm import get_sheet_algorithm_repository


MAX_BATCH_DOCUMENTS = 20
ALLOWED_TOP_LEVEL_FIELDS = {
    "translations",
    "supported_languages",
    "target_country_code",
    "countries",
}


def _load_state() -> tuple[Any, Any, Dict[str, Any]]:
    db = _rest_firestore_client()
    repository = get_sheet_algorithm_repository()
    snapshots = backfill._version_documents(db)
    return db, repository, snapshots


def _rest_firestore_client() -> Any:
    """Use Firestore REST transport because this host's TLS proxy strips gRPC ALPN."""
    load_dotenv(ROOT_DIR / ".env")
    credential_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    if not credential_path or not Path(credential_path).is_file():
        raise FileNotFoundError("FIREBASE_CREDENTIALS_PATH does not point to a service-account file")
    credentials = service_account.Credentials.from_service_account_file(
        credential_path,
        scopes=[backfill.GOOGLE_CLOUD_SCOPE],
    )
    db = firestore.Client(project=credentials.project_id, credentials=credentials)
    transport = FirestoreRestTransport(credentials=credentials)
    db._transport = transport
    db._firestore_api_internal = FirestoreClient(
        transport=transport,
        client_options=db._client_options,
    )
    return db


def _export(limit: int, doc_id: str | None = None) -> None:
    if limit < 1 or limit > MAX_BATCH_DOCUMENTS:
        raise ValueError(f"limit must be between 1 and {MAX_BATCH_DOCUMENTS}")
    if doc_id:
        _db = _rest_firestore_client()
        repository = get_sheet_algorithm_repository()
        snapshot = _db.collection(backfill.COLLECTION).document(doc_id).get()
        snapshots = {doc_id: snapshot} if snapshot.exists else {}
    else:
        _db, repository, snapshots = _load_state()
    documents = []
    unresolved = []
    for snapshot in snapshots.values():
        try:
            plan = backfill._plan(snapshot, repository)
        except Exception as exc:
            unresolved.append({"id": snapshot.id, "reason": str(exc)})
            continue
        if not plan["needs_update"]:
            continue
        translations = plan["data"].get("translations") or {}
        partial = {
            language: translations[language]
            for language in plan["missing"]
            if isinstance(translations, Mapping)
            and isinstance(translations.get(language), Mapping)
        }
        documents.append(
            {
                "id": snapshot.id,
                "scope": plan["data"].get("scope"),
                "country_code": plan["country_code"],
                "source_language": plan["source_language"],
                "source_content": plan["source_content"],
                "missing": plan["missing"],
                "preexisting_aliases": list(plan["aliases"]),
                "existing_partial_translations": partial,
            }
        )
    documents.sort(key=lambda item: (-len(item["missing"]), item["id"]))
    result = {
        "version": backfill.VERSION,
        "documents": documents[:limit],
        "unresolved_count": len(unresolved),
        "unresolved_sample": unresolved[:10],
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def _apply(input_path: Path) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if payload.get("version") != backfill.VERSION:
        raise ValueError("Input batch version does not match 1.1.0")
    items = payload.get("documents")
    if not isinstance(items, list) or not items:
        raise ValueError("Input batch must contain documents")
    if len(items) > MAX_BATCH_DOCUMENTS:
        raise ValueError(f"At most {MAX_BATCH_DOCUMENTS} documents may be applied at once")

    db = _rest_firestore_client()
    repository = get_sheet_algorithm_repository()
    shared_options = payload.get("shared_options") or {}
    allow_partial = bool(payload.get("allow_partial", False))
    prepared = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Each batch item must be an object")
        doc_id = str(item.get("id") or "")
        generated = item.get("translations")
        compact = item.get("compact_translations")
        if generated is None and isinstance(compact, Mapping):
            languages = compact.get("languages")
            if not isinstance(languages, list) or not all(isinstance(code, str) for code in languages):
                raise ValueError(f"Question {doc_id}: compact translations need a language list")
            fields = ("question", "filter_tag", "explanation")
            columns = {field: compact.get(field) for field in fields}
            options_column = compact.get("options")
            for field, values in columns.items():
                if not isinstance(values, list) or len(values) != len(languages):
                    raise ValueError(f"Question {doc_id}: compact {field} column length mismatch")
            if options_column is not None:
                if not isinstance(options_column, Mapping) or any(
                    not isinstance(options_column.get(letter), list)
                    or len(options_column[letter]) != len(languages)
                    for letter in "ABCD"
                ):
                    raise ValueError(f"Question {doc_id}: compact options columns must cover A-D")
            generated = {}
            options_key = compact.get("options_key")
            for index, language in enumerate(languages):
                content = {field: columns[field][index] for field in fields}
                if options_column is not None:
                    content["options"] = {
                        letter: options_column[letter][index] for letter in "ABCD"
                    }
                elif options_key is not None:
                    content["options_key"] = options_key
                else:
                    raise ValueError(f"Question {doc_id}: compact translations need options")
                generated[language] = content
        if not doc_id or not isinstance(generated, Mapping):
            raise ValueError("Each batch item needs an id and translations object")
        expanded = {}
        for language, value in generated.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"Question {doc_id}, {language}: translation must be an object")
            content = dict(value)
            options_key = content.pop("options_key", None)
            if options_key is not None:
                options = shared_options.get(options_key)
                if not isinstance(options, Mapping):
                    raise ValueError(f"Unknown shared options key: {options_key}")
                content["options"] = dict(options)
            expanded[language] = content
        generated = expanded
        reference = db.collection(backfill.COLLECTION).document(doc_id)
        snapshot = reference.get()
        if not snapshot.exists:
            raise ValueError(f"Question {doc_id} no longer exists")
        before = snapshot.to_dict() or {}
        if before.get("dbVersion") != backfill.VERSION and before.get("version") != backfill.VERSION:
            raise ValueError(f"Question {doc_id} is no longer version 1.1.0")
        plan = backfill._plan(snapshot, repository)
        generated_languages = set(generated)
        missing_languages = set(plan["missing"])
        if generated_languages != missing_languages and not (
            allow_partial
            and generated_languages < missing_languages
            and len(generated) == len(generated_languages)
        ):
            raise ValueError(f"Question {doc_id} locale set changed; expected all currently missing languages")

        completed_translations = dict(plan["aliases"])
        completed_translations.update(generated)
        updates: Dict[str, Any] = {}
        old_translations = before.get("translations") or {}
        for language, content in completed_translations.items():
            if not backfill._usable_content(content):
                raise ValueError(f"Question {doc_id}, {language}: invalid question/options shape")
            old_locale = old_translations.get(language) or {}
            old_options = old_locale.get("options") or {}
            for field in ("question", "explanation", "filter_tag"):
                if not str(old_locale.get(field) or "").strip() and str(content.get(field) or "").strip():
                    updates[f"translations.{language}.{field}"] = content[field]
            for letter in "ABCD":
                if (
                    not str(old_options.get(letter) or "").strip()
                    and str(content["options"].get(letter) or "").strip()
                ):
                    updates[f"translations.{language}.options.{letter}"] = content["options"][letter]
        if not updates:
            raise ValueError(f"Question {doc_id}: no missing translation leaves to add")

        available_languages = [
            language
            for language in plan["targets"]
            if language in plan["translations"] or language in generated
        ]
        if list(before.get("supported_languages") or []) != available_languages:
            updates["supported_languages"] = available_languages
        code = plan["country_code"]
        if str(before.get("target_country_code") or "").strip().upper() != code:
            updates["target_country_code"] = code
        if list(before.get("countries") or []) != [code]:
            updates["countries"] = [code]
        if updates:
            prepared.append((reference, before, completed_translations, updates))

    for start in range(0, len(prepared), 450):
        batch = db.batch()
        for reference, _before, _generated, updates in prepared[start : start + 450]:
            batch.update(reference, updates)
        batch.commit()

    for reference, before, generated, _updates in prepared:
        after = reference.get().to_dict() or {}
        saved = after.get("translations") or {}
        for language, content in generated.items():
            merged = saved.get(language) or {}
            if not backfill._usable_content(merged):
                raise RuntimeError(f"Post-write validation failed for {reference.id}/{language}")
            old_locale = (before.get("translations") or {}).get(language) or {}
            old_options = old_locale.get("options") or {}
            for field in ("question", "explanation", "filter_tag"):
                if str(old_locale.get(field) or "").strip() and merged.get(field) != old_locale[field]:
                    raise RuntimeError(f"An existing translation leaf changed: {reference.id}/{language}/{field}")
            for letter in "ABCD":
                if str(old_options.get(letter) or "").strip() and (merged.get("options") or {}).get(letter) != old_options[letter]:
                    raise RuntimeError(f"An existing option changed: {reference.id}/{language}/{letter}")
        for language, old_locale in (before.get("translations") or {}).items():
            if language not in generated and saved.get(language) != old_locale:
                raise RuntimeError(f"An existing locale changed: {reference.id}/{language}")
        for key, old_value in before.items():
            if key not in ALLOWED_TOP_LEVEL_FIELDS and after.get(key) != old_value:
                raise RuntimeError(f"An unrelated question field changed: {reference.id}/{key}")

    print(
        json.dumps(
            {
                "documents_updated": len(prepared),
                "translation_locales_added": sum(len(item[2]) for item in prepared),
                "country_code_fields_are_scoped": ["target_country_code", "countries"],
                "existing_translation_values_preserved": True,
                "unrelated_fields_preserved": True,
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--limit", type=int, default=5)
    export_parser.add_argument("--doc-id", help="Read a single known document without scanning the collection")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "export":
        _export(args.limit, args.doc_id)
    else:
        _apply(args.input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
