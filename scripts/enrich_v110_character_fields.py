"""Analyze and localize character-ability helper fields for v1.1.0 questions.

Gemini Free tier only. Dry-run inventories unfinished records; --apply performs
AI enrichment in resumable batches, global questions first. The script only
replaces the explicit no-op compatibility values created by
backfill_v110_character_fields.py and leaves all other populated fields alone.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

import firebase_admin
from dotenv import load_dotenv
from google.cloud.firestore_v1._helpers import LastUpdateOption

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.translation_service import LANGUAGE_CODE_TO_NAME, SUPPORTED_LANGUAGE_CODES
from database.db_manager import init_firebase
from scripts.backfill_v110_character_fields import (
    ACTIVE_LOCALES,
    EXPECTED_PROJECT,
    MAX_ESTIMATED_DOCUMENT_BYTES,
    QUESTION_VERSION,
    _is_v110,
    _projected_size,
)


STATE_PATH = ROOT_DIR / "data" / ".v110_character_analysis_progress.json"
MAX_ITEMS_PER_REQUEST = 1
SYSTEM_PROMPT = """You are enriching existing multiple-choice trivia for game abilities.
Return one JSON object only, with a `questions` array matching the input order and IDs.

For each question's analysis:
- Option indexes are zero-based: A=0, B=1, C=2, D=3. Never select the correct option as a distractor.
- `weakest_distractor_index` is the least plausible wrong option; `strongest_distractor_index` is the most plausible wrong option. They must be different. Use -1 only if the item is ambiguous, malformed, or has no defensible distractor ranking.
- `logical_order` is the option-index permutation only if the answers genuinely form a chronological, numeric, or logical sequence; otherwise return [] (empty array).
- `option_difficulty_order` is all four indices ordered from easiest to hardest for the stated audience, and must be a permutation. Return [] only if no defensible order can be made.
- If `required_strongest_distractor_index` is an integer, preserve it and write every locale's trap note about that exact wrong option. Otherwise use the strongest index selected in this analysis.

For each supplied locale, use its existing localized stem and options as the source. Do not return or edit the stem/options themselves.
- `simplified_question`: make the question easier to understand without changing facts, conditions, or answer mapping, and without giving away the answer. If it is already simple, return it unchanged.
- `clarified_options`: explain each existing option more clearly, preserve its meaning and A-D position, and do not add clues that change the answer.
- `hint`: a brief, useful clue that does not name the answer. Return empty only when no safe clue is possible.
- `keyword_candidates`: return up to three short phrases copied verbatim from that locale's question; do not translate, inflect, or paraphrase them. Choose phrases worth highlighting and do not reveal an answer that the stem does not already contain.
- `distractor_trap_note`: explain why the strongest wrong option is plausible and why it fails; do not invent facts. Return empty if the explanation is uncertain.
- `binary_fallback`: create a short true statement supported by the correct answer and the given question. Set `is_true` to true. If no safe statement is possible, use an empty statement and false.

Use the exact locale keys supplied in `locale_codes_required`. Every question's `translations` map must contain every one of those locale keys exactly once; do not return only English, and do not copy the sample structure as a one-language limit. Keep each language natural and region-appropriate. Never invent an option, answer, historical fact, or source detail. Return this object shape: `{"questions":[{"question_id":"input ID","analysis":{"weakest_distractor_index":0,"strongest_distractor_index":1,"logical_order":[],"option_difficulty_order":[]},"translations":{"<each required locale code>":{"simplified_question":"...","clarified_options":{"A":"...","B":"...","C":"...","D":"..."},"hint":"...","keyword_candidates":["..."],"distractor_trap_note":"...","binary_fallback":{"statement":"...","is_true":true}}}}]}`. The number of translations must equal `locale_count_expected` for each question."""


def _norm_token(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _tokens_with_spans(text: str) -> list[tuple[str, int, int]]:
    return [(_norm_token(match.group(0)), match.start(), match.end()) for match in re.finditer(r"[^\W_]+", text, re.UNICODE)]


def _exact_question_phrase(candidate: Any, question: str) -> str | None:
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    source_tokens = _tokens_with_spans(question)
    candidate_tokens = [token for token, _start, _end in _tokens_with_spans(candidate)]
    if not candidate_tokens:
        return None
    haystack = [token for token, _start, _end in source_tokens]
    span = len(candidate_tokens)
    for start in range(0, len(haystack) - span + 1):
        if haystack[start : start + span] == candidate_tokens:
            return question[source_tokens[start][1] : source_tokens[start + span - 1][2]]
    return None


def _answer_leaked(text: str, answer: str) -> bool:
    answer_tokens = [token for token, _start, _end in _tokens_with_spans(answer)]
    if not answer_tokens:
        return False
    normalized_text = [token for token, _start, _end in _tokens_with_spans(text)]
    if len(answer_tokens) == 1 and len(answer_tokens[0]) < 5:
        return False
    for start in range(0, len(normalized_text) - len(answer_tokens) + 1):
        if normalized_text[start : start + len(answer_tokens)] == answer_tokens:
            return True
    return False


def _parse_order(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None
    return value if sorted(value) == [0, 1, 2, 3] else None


def _localized_inputs(data: Mapping[str, Any], locales: Sequence[str]) -> dict[str, dict[str, Any]]:
    translations = data.get("translations") or {}
    result: dict[str, dict[str, Any]] = {}
    for locale in locales:
        content = translations.get(locale)
        if not isinstance(content, Mapping):
            continue
        options = content.get("options")
        if not isinstance(options, Mapping):
            continue
        result[locale] = {
            "language": LANGUAGE_CODE_TO_NAME.get(locale, locale),
            "question": str(content.get("question") or ""),
            "options": {key: str(options.get(key) or "") for key in "ABCD"},
            "explanation": str(content.get("explanation") or ""),
        }
    return result


def _is_locale_semantically_done(
    content: Mapping[str, Any], locale_input: Mapping[str, Any], checkpoint_done: bool
) -> bool:
    if checkpoint_done:
        return True
    question = str(locale_input.get("question") or "")
    options = locale_input.get("options") or {}
    simplified = content.get("simplified_question")
    clarified = content.get("clarified_options")
    has_rewrite = (
        isinstance(simplified, str)
        and bool(simplified.strip())
        and simplified.strip() != question.strip()
    )
    clarified_map = (
        {key: str(clarified.get(key) or "").strip() for key in "ABCD"}
        if isinstance(clarified, Mapping)
        else {}
    )
    original_map = {key: str(options.get(key) or "").strip() for key in "ABCD"}
    has_clarification = bool(clarified_map) and clarified_map != original_map
    has_helper = any(str(content.get(key) or "").strip() for key in ("hint", "keyword", "distractor_trap_note"))
    binary = content.get("binary_fallback")
    has_binary = isinstance(binary, Mapping) and bool(str(binary.get("statement") or "").strip())
    return has_rewrite or has_clarification or has_helper or has_binary


def _root_done(data: Mapping[str, Any], checkpoint_done: bool) -> bool:
    if checkpoint_done:
        return True
    correct = str(data.get("correct_option") or data.get("correct_answer") or "A").upper()
    correct_index = "ABCD".find(correct)
    weakest = data.get("weakest_distractor_index")
    strongest = data.get("strongest_distractor_index")
    return (
        isinstance(weakest, int)
        and not isinstance(weakest, bool)
        and isinstance(strongest, int)
        and not isinstance(strongest, bool)
        and weakest in range(4)
        and strongest in range(4)
        and weakest != strongest
        and weakest != correct_index
        and strongest != correct_index
    )


def _make_work_item(snapshot: Any, state: Mapping[str, Any]) -> dict[str, Any] | None:
    data = snapshot.to_dict() or {}
    if not _is_v110(data):
        return None
    doc_state = state.get(snapshot.id, {}) if isinstance(state, Mapping) else {}
    if not isinstance(doc_state, Mapping):
        doc_state = {}
    locales_map = _localized_inputs(data, sorted(ACTIVE_LOCALES))
    done_locales = set(doc_state.get("locales_done") or [])
    pending_locales = [
        locale
        for locale, locale_input in locales_map.items()
        if not _is_locale_semantically_done(
            (data.get("translations") or {}).get(locale, {}),
            locale_input,
            locale in done_locales,
        )
    ]
    root_done = _root_done(data, bool(doc_state.get("root_done")))
    if root_done and not pending_locales:
        return None
    translations = data.get("translations") or {}
    source_content = translations.get("en") or translations.get("tr") or {}
    return {
        "snapshot": snapshot,
        "data": data,
        "id": snapshot.id,
        "root_needed": not root_done,
        "locales": pending_locales,
        "localized_inputs": {locale: locales_map[locale] for locale in pending_locales},
        "source_question": str(source_content.get("question") or ""),
        "source_options": source_content.get("options") or {},
        "source_explanation": str(source_content.get("explanation") or ""),
    }


def _payload_item(item: Mapping[str, Any]) -> dict[str, Any]:
    data = item["data"]
    return {
        "question_id": item["id"],
        "scope": data.get("scope"),
        "target_country": data.get("target_country"),
        "target_country_code": data.get("target_country_code"),
        "difficulty": data.get("difficulty_global") if str(data.get("scope")).lower() in {"global", "gl"} else data.get("difficulty_local"),
        "correct_option": str(data.get("correct_option") or data.get("correct_answer") or "A").upper(),
        "required_strongest_distractor_index": data.get("strongest_distractor_index"),
        "analyze_root_fields": bool(item["root_needed"]),
        "locale_codes_required": list(item["locales"]),
        "locale_count_expected": len(item["locales"]),
        "source_question": item["source_question"],
        "source_options": item["source_options"],
        "source_explanation": item["source_explanation"],
        "locales_to_enrich": item["localized_inputs"],
    }


def _manager_call(items: Sequence[Mapping[str, Any]], manager: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    user_payload = {"questions": [_payload_item(item) for item in items]}
    result = manager.generate_trivia(SYSTEM_PROMPT, json.dumps(user_payload, ensure_ascii=False))
    returned = result.get("questions")
    by_id: dict[str, Any] = {}
    if isinstance(returned, list):
        for item in returned:
            if isinstance(item, Mapping) and item.get("question_id"):
                by_id[str(item["question_id"])] = item
    elif len(items) == 1 and isinstance(returned, Mapping):
        by_id[items[0]["id"]] = returned
    elif len(items) == 1 and isinstance(result.get("translations"), Mapping):
        by_id[items[0]["id"]] = result
    return by_id, result.get("_quota") or {}


def _root_analysis_complete(item: Mapping[str, Any], analysis: Mapping[str, Any] | None) -> bool:
    if not item["root_needed"]:
        return True
    if not isinstance(analysis, Mapping):
        return False
    _updates, done = _root_updates(item, analysis)
    return done


def _generate_complete(item: Mapping[str, Any], manager: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    merged: dict[str, Any] = {"question_id": item["id"], "translations": {}}
    quotas: list[dict[str, Any]] = []
    pending_locales = list(item["locales"])
    root_pending = bool(item["root_needed"])
    request_attempt = 0
    content_attempt = 0
    while request_attempt < 6 and content_attempt < 3:
        request_item = dict(item)
        request_item["root_needed"] = root_pending
        request_item["locales"] = pending_locales
        request_item["localized_inputs"] = {
            locale: item["localized_inputs"][locale] for locale in pending_locales
        }
        request_attempt += 1
        try:
            results, quota = _manager_call([request_item], manager)
        except Exception as exc:
            retry_after = int(getattr(exc, "retry_after", 0) or 0)
            if 0 < retry_after <= 90 and request_attempt < 6:
                delay = retry_after + 1
                print(f"Gemini transient cooldown; retrying this question in {delay}s.")
                time.sleep(delay)
                continue
            raise
        quotas.append(dict(quota))
        content_attempt += 1
        result = results.get(item["id"])
        if not isinstance(result, Mapping):
            continue
        analysis = result.get("analysis")
        if not isinstance(analysis, Mapping):
            analysis = result
        if root_pending and _root_analysis_complete(item, analysis):
            merged["analysis"] = dict(analysis)
            root_pending = False
        returned_translations = result.get("translations")
        if isinstance(returned_translations, Mapping):
            merged["translations"].update(
                {str(locale): value for locale, value in returned_translations.items() if isinstance(value, Mapping)}
            )
        pending_locales = [locale for locale in pending_locales if locale not in merged["translations"]]
        if not pending_locales and not root_pending:
            break
    return merged, quotas


def _root_updates(item: Mapping[str, Any], analysis: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    data = item["data"]
    updates: dict[str, Any] = {}
    correct = str(data.get("correct_option") or data.get("correct_answer") or "A").upper()
    correct_index = "ABCD".find(correct)
    weakest = analysis.get("weakest_distractor_index")
    strongest = analysis.get("strongest_distractor_index")
    valid_distractors = (
        isinstance(weakest, int)
        and not isinstance(weakest, bool)
        and isinstance(strongest, int)
        and not isinstance(strongest, bool)
        and weakest in range(4)
        and strongest in range(4)
        and weakest != strongest
        and weakest != correct_index
        and strongest != correct_index
    )
    if item["root_needed"] and valid_distractors:
        updates["weakest_distractor_index"] = weakest
        updates["strongest_distractor_index"] = strongest
    elif item["root_needed"] and weakest == -1 and strongest == -1:
        updates["weakest_distractor_index"] = None
        updates["strongest_distractor_index"] = None

    if item["root_needed"]:
        if data.get("logical_order") is None:
            updates["logical_order"] = _parse_order(analysis.get("logical_order"))
        if data.get("option_difficulty_order") is None:
            updates["option_difficulty_order"] = _parse_order(analysis.get("option_difficulty_order"))
    return updates, valid_distractors or (weakest == -1 and strongest == -1)


def _locale_updates(
    item: Mapping[str, Any],
    locale: str,
    output: Mapping[str, Any],
    strongest_valid: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    content = (item["data"].get("translations") or {}).get(locale) or {}
    source = item["localized_inputs"][locale]
    question = str(source.get("question") or "")
    options = source.get("options") or {}
    answer_letter = str(item["data"].get("correct_option") or item["data"].get("correct_answer") or "A").upper()
    correct_answer = str(options.get(answer_letter) or "")
    prefix = f"translations.{locale}."
    updates: dict[str, Any] = {}
    stats = {"simplified": 0, "clarified": 0, "hint": 0, "keyword": 0, "trap": 0, "binary": 0}

    simplified = output.get("simplified_question")
    current_simplified = content.get("simplified_question")
    if isinstance(simplified, str) and simplified.strip() and (
        not isinstance(current_simplified, str)
        or not current_simplified.strip()
        or current_simplified.strip() == question.strip()
    ):
        updates[prefix + "simplified_question"] = simplified.strip()
        stats["simplified"] = int(simplified.strip() != question.strip())

    clarified = output.get("clarified_options")
    if isinstance(clarified, Mapping) and all(
        isinstance(clarified.get(key), str) and clarified[key].strip() for key in "ABCD"
    ):
        candidate = {key: clarified[key].strip() for key in "ABCD"}
        current = content.get("clarified_options")
        current_map = {key: str(current.get(key) or "").strip() for key in "ABCD"} if isinstance(current, Mapping) else {}
        original_map = {key: str(options.get(key) or "").strip() for key in "ABCD"}
        if not current_map or current_map == original_map:
            updates[prefix + "clarified_options"] = candidate
            stats["clarified"] = int(candidate != original_map)

    hint = output.get("hint")
    if isinstance(hint, str) and hint.strip() and not str(content.get("hint") or "").strip():
        if not correct_answer or not _answer_leaked(hint, correct_answer):
            updates[prefix + "hint"] = hint.strip()
            stats["hint"] = 1

    candidates = output.get("keyword_candidates")
    if isinstance(candidates, str):
        candidates = [candidates]
    if not isinstance(candidates, list):
        candidates = [output.get("keyword")]
    existing_keyword = str(content.get("keyword") or "").strip()
    if not existing_keyword:
        for candidate in candidates:
            exact = _exact_question_phrase(candidate, question)
            if exact:
                updates[prefix + "keyword"] = exact
                stats["keyword"] = 1
                break

    trap = output.get("distractor_trap_note")
    if strongest_valid and isinstance(trap, str) and trap.strip() and not str(content.get("distractor_trap_note") or "").strip():
        updates[prefix + "distractor_trap_note"] = trap.strip()
        stats["trap"] = 1

    binary = output.get("binary_fallback")
    if isinstance(binary, Mapping) and not content.get("binary_fallback"):
        statement = str(binary.get("statement") or "").strip()
        is_true = binary.get("is_true")
        if statement and isinstance(is_true, bool):
            updates[prefix + "binary_fallback"] = {"statement": statement, "is_true": is_true}
            stats["binary"] = 1
    return updates, stats


def _build_document_update(item: Mapping[str, Any], model_result: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int], bool, list[str]]:
    analysis = model_result.get("analysis")
    if not isinstance(analysis, Mapping):
        analysis = model_result
    updates, root_done = _root_updates(item, analysis)
    translations = model_result.get("translations")
    if not isinstance(translations, Mapping):
        translations = {}
    locale_done: list[str] = []
    stats = {"simplified": 0, "clarified": 0, "hint": 0, "keyword": 0, "trap": 0, "binary": 0}
    strongest = updates.get("strongest_distractor_index", item["data"].get("strongest_distractor_index"))
    correct_letter = str(item["data"].get("correct_option") or item["data"].get("correct_answer") or "A").upper()
    strongest_valid = (
        isinstance(strongest, int)
        and not isinstance(strongest, bool)
        and strongest in range(4)
        and strongest != "ABCD".find(correct_letter)
    )
    for locale in item["locales"]:
        output = translations.get(locale)
        if not isinstance(output, Mapping):
            continue
        locale_patch, locale_stats = _locale_updates(item, locale, output, strongest_valid)
        updates.update(locale_patch)
        for field, count in locale_stats.items():
            stats[field] += count
        # The checkpoint is local-only. It allows intentionally empty nullable
        # helpers to remain null without paying for repeated AI calls.
        locale_done.append(locale)
    return updates, stats, root_done, locale_done


def _apply_patch(reference: Any, update_time: Any, updates: Mapping[str, Any]) -> None:
    try:
        reference.update(dict(updates), option=LastUpdateOption(update_time))
    except Exception:
        # Concurrent writers may have changed other question data. Re-read and
        # merge only if this script's no-op placeholders still exist.
        fresh = reference.get()
        if not fresh.exists:
            raise
        latest = fresh.to_dict() or {}
        safe: dict[str, Any] = {}
        for path, value in updates.items():
            parts = path.split(".")
            current: Any = latest
            for part in parts:
                current = current.get(part) if isinstance(current, Mapping) else None
            if len(parts) == 1:
                if current is None:
                    safe[path] = value
            else:
                if current is None or current == "":
                    safe[path] = value
                elif parts[-1] == "simplified_question":
                    base = latest.get("translations", {}).get(parts[1], {}).get("question")
                    if current == base:
                        safe[path] = value
                elif parts[-1] == "clarified_options":
                    original = latest.get("translations", {}).get(parts[1], {}).get("options")
                    if isinstance(original, Mapping) and current == original:
                        safe[path] = value
        if safe:
            reference.update(safe, option=LastUpdateOption(fresh.update_time))


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "questions": {}}
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("questions"), dict):
        return {"version": 1, "questions": {}}
    return value


def _save_state(state: Mapping[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(STATE_PATH)


def _load_manager() -> Any:
    os.environ["LLM_ALLOWED_PROVIDERS"] = "gemini"
    os.environ["GEMINI_MODELS"] = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    os.environ["GEMINI_QUOTA_GUARD"] = "1"
    os.environ["GEMINI_RPM"] = "8"
    os.environ["GEMINI_TPM"] = "200000"
    os.environ["GEMINI_RPD"] = "450"
    os.environ["GEMINI_MAX_OUTPUT_TOKENS"] = "16384"
    os.environ["GEMINI_TIMEOUT_MS"] = "90000"
    if not os.getenv("GEMINI_API_KEY", "").strip():
        raise RuntimeError("GEMINI_API_KEY is not configured; no paid provider fallback is allowed.")
    from core.llm_manager import get_llm_manager

    return get_llm_manager()


def _ordered_work(db: Any, state: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    work: list[dict[str, Any]] = []
    for snapshot in db.collection("questions").stream():
        item = _make_work_item(snapshot, state.get("questions", {}))
        if item:
            work.append(item)
    work.sort(key=lambda item: (str(item["data"].get("scope") or "global").lower() in {"local"}, item["id"]))
    if limit > 0:
        work = work[:limit]
    return work


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Generate and write AI-enriched fields; otherwise inventory only.")
    parser.add_argument("--project-id", help="Required with --apply; must be knowley-1.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum unfinished question documents to process; 0 means all.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent Gemini requests (capped at 8).")
    args = parser.parse_args()
    if args.apply and args.project_id != EXPECTED_PROJECT:
        parser.error(f"--apply requires --project-id {EXPECTED_PROJECT!r}")
    if args.limit < 0:
        parser.error("--limit must be zero or positive")

    load_dotenv(ROOT_DIR / ".env")
    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    database_name = getattr(db, "_database_string", "")
    if project_id != EXPECTED_PROJECT or not database_name.endswith("/databases/(default)"):
        raise SystemExit(f"Refusing unexpected Firebase target: {project_id} / {database_name}")

    state = _load_state()
    work = _ordered_work(db, state, args.limit)
    globals_total = sum(str(item["data"].get("scope") or "global").lower() in {"global", "gl"} for item in work)
    locals_total = len(work) - globals_total
    locale_count = sum(len(item["locales"]) for item in work)
    print(
        f"Project: {project_id}; database: {database_name}; unfinished docs selected: {len(work)} "
        f"(global {globals_total}, local {locals_total}); locale records pending: {locale_count}; "
        f"Gemini workers: {min(8, max(1, args.workers))}"
    )
    if not args.apply:
        print("Read-only inventory complete. Use --apply --project-id knowley-1 to enrich the selected records.")
        return 0
    if not work:
        print("No unfinished semantic fields found.")
        return 0

    manager = _load_manager()
    workers = min(8, max(1, args.workers))
    groups = [work[start : start + MAX_ITEMS_PER_REQUEST] for start in range(0, len(work), MAX_ITEMS_PER_REQUEST)]
    progress_lock = threading.Lock()
    totals = {"documents": 0, "root_indices": 0, "root_orders": 0, "locales": 0, "simplified": 0, "clarified": 0, "hints": 0, "keywords": 0, "traps": 0, "binary": 0, "failed": 0, "requests": 0, "input_tokens": 0, "output_tokens": 0}
    quota_pause: list[int] = []

    def process_group(group: Sequence[Mapping[str, Any]]) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
        processed = 0
        quotas: list[dict[str, Any]] = []
        group_totals: dict[str, Any] = {"documents": 0, "root_indices": 0, "root_orders": 0, "locales": 0, "simplified": 0, "clarified": 0, "hints": 0, "keywords": 0, "traps": 0, "binary": 0, "failed": 0}
        for item in group:
            model_result, item_quotas = _generate_complete(item, manager)
            quotas.extend(item_quotas)
            updates, stats, root_done, locale_done = _build_document_update(item, model_result)
            if not item["locales"]:
                root_done = root_done or item["root_needed"]
            if not root_done and item["root_needed"]:
                group_totals["failed"] += 1
                print(f"Root analysis invalid for {item['id']}; record left resumable.")
                continue
            if item["locales"] and not locale_done:
                group_totals["failed"] += 1
                print(f"No valid localized helpers for {item['id']}; record left resumable.")
                continue
            if updates:
                if _projected_size(item["data"], updates) > MAX_ESTIMATED_DOCUMENT_BYTES:
                    group_totals["failed"] += 1
                    print(f"Projected Firestore document size limit exceeded for {item['id']}.")
                    continue
                _apply_patch(item["snapshot"].reference, item["snapshot"].update_time, updates)
            with progress_lock:
                latest_state = _load_state()
                question_states = latest_state.setdefault("questions", {})
                doc_state = question_states.setdefault(item["id"], {"locales_done": []})
                if root_done:
                    doc_state["root_done"] = True
                completed = set(doc_state.get("locales_done") or [])
                completed.update(locale_done)
                doc_state["locales_done"] = sorted(completed)
                doc_state["updated_at"] = int(time.time())
                _save_state(latest_state)
            group_totals["documents"] += 1
            group_totals["locales"] += len(locale_done)
            group_totals["simplified"] += stats["simplified"]
            group_totals["clarified"] += stats["clarified"]
            group_totals["hints"] += stats["hint"]
            group_totals["keywords"] += stats["keyword"]
            group_totals["traps"] += stats["trap"]
            group_totals["binary"] += stats["binary"]
            group_totals["root_indices"] += int(
                "weakest_distractor_index" in updates and updates.get("weakest_distractor_index") is not None
            )
            group_totals["root_orders"] += int(
                updates.get("logical_order") is not None or updates.get("option_difficulty_order") is not None
            )
            processed += 1
        return processed, group_totals, quotas

    # Submit one small wave at a time. If Gemini reports the free quota ceiling,
    # stop cleanly; the local checkpoint resumes the remainder next run/day.
    for wave_start in range(0, len(groups), workers):
        wave = groups[wave_start : wave_start + workers]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_group, group): group for group in wave}
            for future in as_completed(futures):
                try:
                    _processed, result, quotas = future.result()
                    for key in result:
                        totals[key] += result[key]
                    totals["requests"] += len(quotas)
                    totals["input_tokens"] += sum(int(quota.get("input_tokens") or 0) for quota in quotas)
                    totals["output_tokens"] += sum(int(quota.get("output_tokens") or 0) for quota in quotas)
                except Exception as exc:
                    retry_after = getattr(exc, "retry_after", 0)
                    quota_limited = bool(getattr(exc, "quota_limited", False))
                    if quota_limited and retry_after > 300:
                        quota_pause.append(int(retry_after))
                        print(f"Gemini free quota reached; stopping cleanly (retry-after {int(retry_after)}s).")
                    else:
                        totals["failed"] += 1
                        print(f"Gemini batch failed: {type(exc).__name__}: {str(exc)[:220]}")
        if quota_pause:
            break
        if totals["requests"] and totals["requests"] % 10 == 0:
            print(
                f"Progress: docs {totals['documents']}, requests {totals['requests']}, "
                f"locales {totals['locales']}, output tokens {totals['output_tokens']}."
            )

    print("AI enrichment summary:", json.dumps(totals, ensure_ascii=False))
    if quota_pause:
        print("Run the same command again after the free quota resets to resume from the local checkpoint.")
        return 0
    return 0 if totals["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
