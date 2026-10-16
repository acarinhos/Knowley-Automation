"""Bring existing 1.1.0 Firestore question translations up to the current rules.

The default mode is a read-only inventory. Pass ``--apply`` to translate and
update documents in the configured ``knowley-1`` Firestore project's default
database. The operation is resumable: complete documents are skipped.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from dotenv import load_dotenv
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from google.cloud.firestore_v1._helpers import LastUpdateOption

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.sheet_algorithm import get_sheet_algorithm_repository
from core.translation_service import (
    SUPPORTED_LANGUAGE_CODES,
    translate_localized_content,
    translate_localized_content_batch,
)
from database.db_manager import init_firebase


VERSION = "1.1.0"
COLLECTION = "questions"
GOOGLE_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
LEGACY_COUNTRY_ALIASES = {
    _name: _code
    for _name, _code in (
        ("Orta Doğu (Mısır hariç)", "ME"),
        ("Orta Doğu (Mısır Hariç)", "ME"),
        ("İskandinav Bölgesi", "SCA"),
        ("Brezilya", "LATAM"),
        ("Brazil", "LATAM"),
    )
}
logger = logging.getLogger("FirestoreTranslationBackfill")

# Reuse already stored base-language translations when adding the requested
# regional locale keys. This keeps the backfill resumable and avoids repeating
# equivalent translations for locales differing only by region.
# Canonical short code -> older regional key that may hold the same translation.
LEGACY_LOCALE_SOURCES = {
    "de": "de-DE", "bn": "bn-BD", "da": "da-DK", "fa": "fa-IR", "nl": "nl-NL", "fi": "fi-FI", "fr": "fr-FR",
    "hi": "hi-IN", "ja": "ja-JP", "ko": "ko-KR", "pl": "pl-PL", "hu": "hu-HU", "ms": "ms-MY", "no": "nb",
    "pt": "pt-BR", "ru": "ru-RU", "tr": "tr-TR", "el": "el-GR", "cs": "cs-CZ", "zh": "zh-Hans",
    "zh-TW": "zh-Hant", "he": "iw-IL", "es": "es-ES", "sv": "sv-SE", "it": "it-IT", "es-LA": "es-419",
}


def _version_documents(db: Any) -> Dict[str, Any]:
    from google.cloud.firestore_v1.base_query import FieldFilter

    documents: Dict[str, Any] = {}
    collection = db.collection(COLLECTION)
    for field in ("dbVersion", "version"):
        query = collection.where(filter=FieldFilter(field, "==", VERSION))
        for snapshot in query.stream():
            documents[snapshot.id] = snapshot
    return documents


def _inspect_default_database() -> Mapping[str, Any]:
    load_dotenv(ROOT_DIR / ".env")
    credential_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    if not credential_path or not Path(credential_path).is_file():
        raise FileNotFoundError("FIREBASE_CREDENTIALS_PATH does not point to a service-account file")
    credential = service_account.Credentials.from_service_account_file(
        credential_path,
        scopes=[GOOGLE_CLOUD_SCOPE],
    )
    project_id = credential.project_id
    session = AuthorizedSession(credential)
    response = session.get(
        f"https://firestore.googleapis.com/v1/projects/{project_id}/databases",
        timeout=20,
    )
    response.raise_for_status()
    databases = response.json().get("databases", [])
    default_database = next(
        (item for item in databases if item.get("name", "").endswith("/databases/(default)")),
        None,
    )
    if not default_database:
        raise RuntimeError(f"No default Firestore database found in project {project_id}")
    return default_database


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return "".join(char for char in normalized if char.isalnum())


def _country_for_question(data: Mapping[str, Any], repository: Any) -> Optional[Dict[str, Any]]:
    countries = repository.countries()
    by_code = {str(item.get("code", "")).upper(): item for item in countries}
    by_name = {_fold(item.get("name")): item for item in countries}
    names = [data.get("target_country")]
    listed = data.get("countries")
    names.extend(listed if isinstance(listed, list) else [listed])

    code = str(data.get("target_country_code") or "").strip().upper()
    if code in by_code:
        return by_code[code]
    for name in names:
        candidate = str(name or "").strip()
        if candidate.upper() in by_code:
            return by_code[candidate.upper()]
        alias_code = LEGACY_COUNTRY_ALIASES.get(candidate)
        if alias_code in by_code:
            return by_code[alias_code]
        if _fold(candidate) in by_name:
            return by_name[_fold(candidate)]
    return None


def _target_languages(data: Mapping[str, Any], repository: Any) -> tuple[list[str], str]:
    scope = str(data.get("scope") or "").strip().lower()
    country = _country_for_question(data, repository)
    if scope == "local" or (scope not in {"global", "gl"} and country is not None):
        if country is None:
            raise ValueError("Local question has no country/region row to resolve its languages")
        languages = repository.languages_for_country(country["name"])
        if not languages:
            languages = ["en"]
        elif "en" not in languages:
            languages = [*languages, "en"]
        return languages, str(country["code"])
    return list(SUPPORTED_LANGUAGE_CODES), "GL"


def _usable_content(value: Any) -> bool:
    if not isinstance(value, Mapping) or not str(value.get("question") or "").strip():
        return False
    options = value.get("options")
    return isinstance(options, Mapping) and all(
        str(options.get(letter) or "").strip() for letter in "ABCD"
    )


def _source_translation(
    translations: Mapping[str, Any], target_languages: Sequence[str]
) -> tuple[str, Dict[str, Any]]:
    for preferred in ("en", "tr"):
        content = translations.get(preferred)
        if _usable_content(content):
            return preferred, dict(content)
    for language in target_languages:
        content = translations.get(language)
        if _usable_content(content):
            return language, dict(content)
    for language, content in translations.items():
        if _usable_content(content):
            return str(language), dict(content)
    raise ValueError("No usable source translation with a question and four options")


def _translation_characters(content: Mapping[str, Any]) -> int:
    options = content.get("options")
    values = [content.get("question"), content.get("explanation"), content.get("filter_tag")]
    if isinstance(options, Mapping):
        values.extend(options.get(letter, "") for letter in "ABCD")
    return sum(len(str(value or "")) for value in values)


def _plan(snapshot: Any, repository: Any) -> Dict[str, Any]:
    data = snapshot.to_dict() or {}
    targets, country_code = _target_languages(data, repository)
    translations = data.get("translations")
    if not isinstance(translations, Mapping):
        translations = {}
    source_language, source_content = _source_translation(translations, targets)
    valid_existing = {
        language: translations[language]
        for language in targets
        if _usable_content(translations.get(language))
    }
    aliased = {}
    for language in targets:
        if language in valid_existing:
            continue
        legacy_language = LEGACY_LOCALE_SOURCES.get(language)
        legacy_content = translations.get(legacy_language) if legacy_language else None
        if _usable_content(legacy_content):
            aliased[language] = dict(legacy_content)
    resolved = {**valid_existing, **aliased}
    missing = [language for language in targets if language not in resolved]
    chars = _translation_characters(source_content)
    return {
        "data": data,
        "targets": targets,
        "country_code": country_code,
        "translations": resolved,
        "aliases": aliased,
        "missing": missing,
        "source_language": source_language,
        "source_content": source_content,
        "estimated_characters": chars * len(missing),
        "needs_update": (
            bool(missing)
            or bool(aliased)
            or list(data.get("supported_languages") or []) != targets
            or str(data.get("target_country_code") or "") != country_code
            or list(data.get("countries") or []) != [country_code]
        ),
    }


def _apply_one(
    snapshot: Any,
    repository: Any,
    plan: Mapping[str, Any],
    *,
    prefer_azure: bool = False,
) -> str:
    translations = dict(plan["translations"])
    missing = list(plan["missing"])
    provider = str(plan["data"].get("translation_provider") or "")
    if missing:
        generated, provider = translate_localized_content(
            plan["source_content"],
            plan["source_language"],
            missing,
            prefer_azure=prefer_azure,
            llm_batch_size=8,
        )
        translations.update(generated)
    return _store_translations(snapshot, plan, translations, provider)


def _store_translations(
    snapshot: Any,
    plan: Mapping[str, Any],
    translations: Mapping[str, Any],
    provider: str,
    *,
    allow_partial: bool = False,
) -> str:
    data = plan["data"]
    current_translations = data.get("translations")
    if not isinstance(current_translations, Mapping):
        current_translations = {}

    updates: Dict[str, Any] = {}
    available_languages: list[str] = []
    for language in plan["targets"]:
        generated = translations.get(language)
        existing = current_translations.get(language)
        if _usable_content(existing):
            available_languages.append(language)
        if not _usable_content(generated):
            if language not in available_languages and not allow_partial:
                raise ValueError(f"Translation result is missing required language {language}")
            continue

        old_locale = existing if isinstance(existing, Mapping) else {}
        old_options = old_locale.get("options")
        old_options = old_options if isinstance(old_options, Mapping) else {}
        new_options = generated.get("options")
        new_options = new_options if isinstance(new_options, Mapping) else {}
        for field in ("question", "explanation", "filter_tag"):
            if not str(old_locale.get(field) or "").strip() and str(generated.get(field) or "").strip():
                updates[f"translations.{language}.{field}"] = generated[field]
        for letter in "ABCD":
            if not str(old_options.get(letter) or "").strip() and str(new_options.get(letter) or "").strip():
                updates[f"translations.{language}.options.{letter}"] = new_options[letter]
        if language not in available_languages:
            available_languages.append(language)

    if not allow_partial and set(available_languages) != set(plan["targets"]):
        raise ValueError("Translation result does not cover every required language")

    current_supported = list(data.get("supported_languages") or [])
    ordered_available = [language for language in plan["targets"] if language in available_languages]
    if current_supported != ordered_available:
        updates["supported_languages"] = ordered_available
    country_code = str(plan["country_code"])
    if str(data.get("target_country_code") or "").strip().upper() != country_code:
        updates["target_country_code"] = country_code
    if list(data.get("countries") or []) != [country_code]:
        updates["countries"] = [country_code]

    if updates:
        if snapshot.update_time is None:
            raise RuntimeError(f"Question {snapshot.id} has no Firestore update-time precondition")
        snapshot.reference.update(updates, option=LastUpdateOption(snapshot.update_time))
    return provider or "existing"


def _apply_translation_batch(
    items: Sequence[tuple[Any, Mapping[str, Any]]],
) -> tuple[int, list[tuple[str, str]]]:
    completed = 0
    failures: list[tuple[str, str]] = []

    def save_result(
        snapshot: Any,
        plan: Mapping[str, Any],
        translations: Mapping[str, Any],
        provider: str,
    ) -> None:
        nonlocal completed
        if not (set(translations) - set(plan["translations"])):
            # Provider failures must not produce Firestore writes that add no
            # locale data. A later run can resume cleanly when a provider works.
            return
        try:
            _store_translations(snapshot, plan, translations, provider)
            completed += 1
        except Exception as exc:
            if set(translations) >= set(plan["targets"]):
                failures.append((snapshot.id, str(exc)))
                logger.error("Failed %s while saving: %s", snapshot.id, exc)
                return
            missing = [language for language in plan["targets"] if language not in translations]
            try:
                _store_translations(
                    snapshot,
                    plan,
                    translations,
                    provider,
                    allow_partial=True,
                )
                reason = "incomplete languages: " + ", ".join(missing)
                failures.append((snapshot.id, reason))
                logger.warning("Partially saved %s; %s", snapshot.id, reason)
            except Exception as partial_error:
                failures.append((snapshot.id, str(partial_error)))
                logger.error("Failed %s while saving: %s", snapshot.id, partial_error)

    grouped_fallbacks: Dict[tuple[str, ...], list[Dict[str, Any]]] = {}
    for snapshot, plan in items:
        translations = dict(plan["translations"])
        fallback_languages = list(plan["missing"])
        previous_provider = str(plan["data"].get("translation_provider") or "")
        provider = previous_provider
        if fallback_languages:
            grouped_fallbacks.setdefault(tuple(fallback_languages), []).append(
                {
                    "snapshot": snapshot,
                    "plan": plan,
                    "translations": translations,
                    "provider": provider,
                }
            )
            continue
        save_result(snapshot, plan, translations, provider)

    # Reservations are serialized by the quota ledger; two in-flight calls
    # keep generation time down while remaining under each model's RPM cap.
    worker_count = 2

    # One-question requests keep multilingual output below the observed Gemini
    # gateway timeout. Two workers use the free model RPM safely.
    job_chunks = iter(
        group[start : start + 1]
        for group in grouped_fallbacks.values()
        for start in range(0, len(group), 1)
    )
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {}
        transient_retries: Counter[tuple[str, ...]] = Counter()

        def submit_next() -> bool:
            try:
                chunk = next(job_chunks)
            except StopIteration:
                return False
            future = executor.submit(
                translate_localized_content_batch,
                [entry["plan"]["source_content"] for entry in chunk],
                str(chunk[0]["plan"]["source_language"]),
                chunk[0]["plan"]["missing"],
                item_batch_size=1,
            )
            futures[future] = chunk
            return True

        for _ in range(worker_count):
            if not submit_next():
                break

        while futures:
            done, _pending = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                chunk = futures.pop(future)
                try:
                    generated_translations, llm_provider = future.result()
                except Exception as exc:
                    retryable = getattr(exc, "retryable", False)
                    quota_limited = getattr(exc, "quota_limited", False)
                    retry_key = tuple(entry["snapshot"].id for entry in chunk)
                    if retryable and quota_limited:
                        delay = max(1, int(exc.retry_after))
                        logger.warning(
                            "Gemini kota beklemesi: %ds; aynı %d soruluk parça tekrar denenecek.",
                            delay,
                            len(chunk),
                        )
                        time.sleep(delay)
                        futures[executor.submit(
                            translate_localized_content_batch,
                            [entry["plan"]["source_content"] for entry in chunk],
                            str(chunk[0]["plan"]["source_language"]),
                            chunk[0]["plan"]["missing"],
                            item_batch_size=len(chunk),
                        )] = chunk
                        continue
                    if retryable and len(chunk) == 1 and transient_retries[retry_key] < 3:
                        transient_retries[retry_key] += 1
                        delay = max(1, int(getattr(exc, "retry_after", 30)))
                        logger.warning(
                            "Transient Gemini error; retrying one-question request in %ds (%d/3): %s",
                            delay,
                            transient_retries[retry_key],
                            str(exc)[:180],
                        )
                        time.sleep(delay)
                        futures[executor.submit(
                            translate_localized_content_batch,
                            [entry["plan"]["source_content"] for entry in chunk],
                            str(chunk[0]["plan"]["source_language"]),
                            chunk[0]["plan"]["missing"],
                            item_batch_size=1,
                        )] = chunk
                        continue
                    if len(chunk) > 1:
                        midpoint = len(chunk) // 2
                        smaller_chunks = [chunk[:midpoint], chunk[midpoint:]]
                        logger.warning(
                            "%d soruluk çeviri isteği başarısız; daha küçük partilere bölünüyor: %s",
                            len(chunk),
                            str(exc)[:180],
                        )
                        for smaller in reversed(smaller_chunks):
                            futures[executor.submit(
                                translate_localized_content_batch,
                                [entry["plan"]["source_content"] for entry in smaller],
                                str(smaller[0]["plan"]["source_language"]),
                                smaller[0]["plan"]["missing"],
                                item_batch_size=len(smaller),
                            )] = smaller
                        continue
                    for entry in chunk:
                        snapshot = entry["snapshot"]
                        failures.append((snapshot.id, str(exc)))
                        logger.error("Failed %s: %s", snapshot.id, exc)
                        save_result(
                            snapshot,
                            entry["plan"],
                            entry["translations"],
                            entry["provider"],
                        )
                    continue
                for entry, generated in zip(chunk, generated_translations):
                    snapshot = entry["snapshot"]
                    plan = entry["plan"]
                    translations = entry["translations"]
                    translations.update(generated)
                    provider = "+".join(
                        dict.fromkeys(
                            part
                            for part in [entry["provider"], *str(llm_provider).split("+")]
                            if part
                        )
                    )
                    save_result(snapshot, plan, translations, provider)

            while len(futures) < worker_count and submit_next():
                pass
    return completed, failures


def _changed_work(
    db: Any,
    repository: Any,
    limit: int = 0,
) -> tuple[list[tuple[Any, Mapping[str, Any]]], list[tuple[str, str]], int, Counter[str]]:
    snapshots = list(_version_documents(db).values())
    changed: list[tuple[Any, Mapping[str, Any]]] = []
    unresolved: list[tuple[str, str]] = []
    scope_counts: Counter[str] = Counter()
    for snapshot in snapshots:
        try:
            plan = _plan(snapshot, repository)
            scope_counts["local" if plan["country_code"] != "GL" else "global"] += 1
            if plan["needs_update"]:
                changed.append((snapshot, plan))
        except Exception as exc:
            unresolved.append((snapshot.id, str(exc)))
    changed.sort(
        key=lambda item: (
            item[1]["country_code"] != "GL",
            -len(item[1]["missing"]),
            item[0].id,
        )
    )
    if limit > 0:
        changed = changed[:limit]
    return changed, unresolved, len(snapshots), scope_counts


def _apply_changed(
    changed: Sequence[tuple[Any, Mapping[str, Any]]],
    repository: Any,
) -> tuple[int, list[tuple[str, str]]]:
    completed = 0
    failures: list[tuple[str, str]] = []
    translation_groups: Dict[tuple[str, str, tuple[str, ...]], list[tuple[Any, Mapping[str, Any]]]] = {}
    metadata_only = []
    for snapshot, plan in changed:
        if not plan["missing"]:
            metadata_only.append((snapshot, plan))
            continue
        key = (
            plan["country_code"],
            str(plan["source_language"]),
            tuple(plan["missing"]),
        )
        translation_groups.setdefault(key, []).append((snapshot, plan))

    for index, (snapshot, plan) in enumerate(metadata_only, start=1):
        try:
            _apply_one(snapshot, repository, plan, prefer_azure=False)
            completed += 1
        except Exception as exc:
            failures.append((snapshot.id, str(exc)))
            logger.error("Failed metadata update %s (%d/%d): %s", snapshot.id, index, len(changed), exc)

    processed = len(metadata_only)
    for group in translation_groups.values():
        for start in range(0, len(group), 2):
            batch = group[start : start + 2]
            batch_completed, batch_failures = _apply_translation_batch(batch)
            completed += batch_completed
            failures.extend(batch_failures)
            processed += len(batch)
            if processed % 20 < 2 or processed == len(changed):
                logger.info(
                    "Gemini-only progress: updated %d/%d changed docs; processed %d/%d",
                    completed,
                    len(changed),
                    processed,
                    len(changed),
                )
    return completed, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Translate and update matching Firestore documents")
    parser.add_argument("--limit", type=int, default=0, help="Optionally limit the number of documents needing translation")
    parser.add_argument(
        "--gemini-only",
        "--llm-only",
        dest="gemini_only",
        action="store_true",
        help="Use the Gemini free-tier models only (all --apply runs are Gemini-only)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database = _inspect_default_database()
    database_name = str(database.get("name", ""))
    database_edition = str(
        database.get("databaseEdition")
        or database.get("database_edition")
        or "STANDARD (edition field omitted; Google default)"
    )
    logger.info("Firestore target: %s; edition/type: %s", database_name, database_edition)

    db = init_firebase()
    repository = get_sheet_algorithm_repository()
    changed, unresolved, total_documents, scope_counts = _changed_work(db, repository, args.limit)
    missing_languages = sum(len(plan["missing"]) for _snapshot, plan in changed)
    estimated_characters = sum(
        plan["estimated_characters"] for _snapshot, plan in changed
    )
    logger.info("Matched exact version %s: %d documents", VERSION, total_documents)
    logger.info("Scope counts: %s", dict(scope_counts))
    logger.info("Documents needing updates: %d", len(changed))
    logger.info("Missing translation slots: %d", missing_languages)
    logger.info("Estimated source characters across missing translation slots: %d", estimated_characters)
    logger.info("Unresolved documents: %d", len(unresolved))
    for doc_id, reason in unresolved[:20]:
        logger.warning("Unresolved %s: %s", doc_id, reason)

    if not args.apply:
        logger.info("Read-only inventory complete. Re-run with --apply to write updates.")
        return 0

    # Scope these settings to this process. They deliberately do not overwrite
    # the user's .env values or change provider routing in the desktop app.
    os.environ["LLM_ALLOWED_PROVIDERS"] = "gemini"
    os.environ["GEMINI_MODELS"] = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    os.environ["GEMINI_QUOTA_GUARD"] = "1"
    os.environ["GEMINI_RPM"] = "8"
    os.environ["GEMINI_TPM"] = "200000"
    os.environ["GEMINI_RPD"] = "450"
    os.environ["GEMINI_MAX_OUTPUT_TOKENS"] = "65536"
    os.environ["BACKFILL_TRANSLATION_WORKERS"] = "2"
    if not os.getenv("GEMINI_API_KEY", "").strip():
        logger.error("GEMINI_API_KEY is missing; no translation provider will be used as fallback.")
        return 2

    completed_total = 0
    failures_total: list[tuple[str, str]] = []
    pass_number = 0
    while changed:
        pass_number += 1
        pending_work = sum(max(1, len(plan["missing"])) for _snapshot, plan in changed)
        completed, failures = _apply_changed(changed, repository)
        completed_total += completed
        failures_total.extend(failures)
        if args.limit > 0:
            changed = []
            break

        next_changed, next_unresolved, _total_documents, _scope_counts = _changed_work(db, repository)
        next_pending_work = sum(max(1, len(plan["missing"])) for _snapshot, plan in next_changed)
        if not next_changed:
            changed = []
            unresolved = next_unresolved
            failures_total = []
            break
        if next_pending_work >= pending_work:
            logger.error(
                "Translation pass %d made no measurable progress (%d pending items remain); stopping so this run cannot spin forever.",
                pass_number,
                len(next_changed),
            )
            failures_total.append(("progress", "Pending translation work did not decrease after a full pass"))
            changed = next_changed
            unresolved = next_unresolved
            break
        changed = next_changed
        unresolved = next_unresolved
        # Earlier incomplete-part errors are recoverable after their saved
        # fields are rescanned; only report failures that survive the final pass.
        failures_total = []
        logger.info(
            "Pass %d saved progress; %d documents / %d translation fields remain. Continuing from Firebase state.",
            pass_number,
            len(changed),
            sum(len(plan["missing"]) for _snapshot, plan in changed),
        )

    logger.info(
        "Updated: %d this run; failed: %d; unresolved country/source: %d; pending documents: %d",
        completed_total,
        len(failures_total),
        len(unresolved),
        len(changed),
    )
    return 1 if failures_total or unresolved or changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
