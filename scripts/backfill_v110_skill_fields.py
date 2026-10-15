"""Fill missing skill-support fields on existing 1.1.0 Firestore questions.

Fields: per locale hint, keyword, distractor_trap_note, binary_fallback, simplified_question,
clarified_options; root weakest/strongest_distractor_index and option_difficulty_order.

Dry-run by default (read-only inventory + cost estimate). --apply needs --project-id knowley-1.
Only MISSING fields are written (field-path updates with an update-time precondition), so
question, options, correct_answer, is_active and statistics can never change. A local backup
of the processed documents is written first (local_backups/, not committed) and finished
document ids go to a progress file so --resume skips them.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

LOCALE_TEXT_FIELDS = ("hint", "keyword", "distractor_trap_note", "simplified_question")
ROOT_FIELDS = ("weakest_distractor_index", "strongest_distractor_index", "option_difficulty_order")
BACKUP_DIR = ROOT_DIR / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "v110_skill_backfill_progress.json"
logger = logging.getLogger("SkillFieldBackfill")


def _text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _options_complete(value: Any) -> bool:
    return isinstance(value, Mapping) and all(_text(value.get(k)) for k in "ABCD")


def _binary_complete(value: Any) -> bool:
    return isinstance(value, Mapping) and _text(value.get("statement")) and isinstance(value.get("is_true"), bool)


def valid_order(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, int) for v in value) and sorted(value) == [0, 1, 2, 3]


def _field_ok(field: str, value: Any) -> bool:
    if field == "binary_fallback":
        return _binary_complete(value)
    if field == "clarified_options":
        return _options_complete(value)
    return _text(value)


def missing_locale_fields(content: Mapping[str, Any]) -> List[str]:
    return [f for f in (*LOCALE_TEXT_FIELDS, "binary_fallback", "clarified_options") if not _field_ok(f, content.get(f))]


def missing_fields(data: Mapping[str, Any]) -> Dict[str, Any]:
    """{"root": [...], "locales": {locale: [fields]}} for locales that have a usable question."""
    root = [f for f in ROOT_FIELDS[:2] if data.get(f) is None]
    if not valid_order(data.get("option_difficulty_order")):
        root.append("option_difficulty_order")
    locales = {}
    for loc, content in (data.get("translations") or {}).items():
        if isinstance(content, Mapping) and _text(content.get("question")) and _options_complete(content.get("options")):
            gaps = missing_locale_fields(content)
            if gaps:
                locales[loc] = gaps
    return {"root": root, "locales": locales}


def pick_source_language(data: Mapping[str, Any]) -> Optional[str]:
    translations = data.get("translations") or {}
    for loc in ("en", "tr", *translations):
        content = translations.get(loc)
        if isinstance(content, Mapping) and _text(content.get("question")) and _options_complete(content.get("options")):
            return loc
    return None


def check_support(correct_answer: Any, root: Mapping[str, Any], source: Mapping[str, Any], language: str) -> None:
    """Validate with the generator's own rule: all text fields present, indices distinct and != answer."""
    from core.generator import validate_skill_support_fields

    fallback = source.get("binary_fallback")
    content = SimpleNamespace(
        hint=source.get("hint"), keyword=source.get("keyword"), distractor_trap_note=source.get("distractor_trap_note"),
        binary_fallback=SimpleNamespace(**fallback) if isinstance(fallback, Mapping) else None,
    )
    question = SimpleNamespace(
        translations={language: content}, correct_answer=correct_answer,
        weakest_distractor_index=root.get("weakest_distractor_index"),
        strongest_distractor_index=root.get("strongest_distractor_index"),
    )
    validate_skill_support_fields(question, language)


def build_updates(gaps: Mapping[str, Any], root_values: Mapping[str, Any],
                  generated: Mapping[str, Mapping[str, Any]]) -> Dict[tuple, Any]:
    """Field-path tuple -> value, containing ONLY currently-missing fields that now have valid values."""
    updates: Dict[tuple, Any] = {}
    for field in gaps["root"]:
        value = root_values.get(field)
        if value is not None and (field != "option_difficulty_order" or valid_order(value)):
            updates[(field,)] = value
    for loc, fields in gaps["locales"].items():
        content = generated.get(loc) or {}
        for field in fields:
            if _field_ok(field, content.get(field)):
                updates[("translations", loc, field)] = content[field]
    return updates


def generation_prompt(data: Mapping[str, Any], language: str, content: Mapping[str, Any]) -> str:
    opts = content["options"]
    fixed = {k: data.get(k) for k in ROOT_FIELDS[:2] if data.get(k) is not None}
    return (
        f"Write in language code '{language}'. Trivia question: {content['question']}\n"
        f"Options: A) {opts['A']} B) {opts['B']} C) {opts['C']} D) {opts['D']}\n"
        f"Correct answer: {data.get('correct_answer')}. Explanation: {content.get('explanation', '')}\n"
        f"Already fixed values (keep exactly): {json.dumps(fixed)}\n"
        "Return JSON with: hint (short, does not reveal the answer), keyword (key phrase from the question), "
        "distractor_trap_note (why the strongest wrong option is wrong), binary_fallback {statement, is_true} (one short "
        "verifiable statement), simplified_question (same facts and answer, simpler wording), clarified_options {A,B,C,D} "
        "(clearer wording, same meaning and keys), weakest_distractor_index and strongest_distractor_index (0-based A=0..D=3, "
        "both wrong options, different from each other and from the correct answer), option_difficulty_order (permutation of "
        "[0,1,2,3], easiest to hardest to recognise). Do not add new facts."
    )


def _option(prop: str = "STRING") -> Dict[str, Any]:
    return {"type": "OBJECT", "properties": {k: {"type": prop} for k in "ABCD"}, "required": list("ABCD")}


GEN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hint": {"type": "STRING"}, "keyword": {"type": "STRING"}, "distractor_trap_note": {"type": "STRING"},
        "binary_fallback": {"type": "OBJECT", "properties": {"statement": {"type": "STRING"}, "is_true": {"type": "BOOLEAN"}},
                            "required": ["statement", "is_true"]},
        "simplified_question": {"type": "STRING"}, "clarified_options": _option(),
        "weakest_distractor_index": {"type": "INTEGER"}, "strongest_distractor_index": {"type": "INTEGER"},
        "option_difficulty_order": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    },
    "required": ["hint", "keyword", "distractor_trap_note", "binary_fallback", "simplified_question",
                 "clarified_options", "weakest_distractor_index", "strongest_distractor_index"],
}


def process_doc(data: Mapping[str, Any], llm_manager: Any) -> Dict[tuple, Any]:
    """Generate the missing fields for one document; ValueError if the result is not valid."""
    from core.translation_service import translate_localized_content

    gaps = missing_fields(data)
    language = pick_source_language(data)
    if language is None:
        raise ValueError("no usable source translation")
    source = dict(data["translations"][language])
    root = {f: data.get(f) for f in ROOT_FIELDS}
    if not valid_order(root["option_difficulty_order"]):
        root["option_difficulty_order"] = None
    if gaps["root"] or language in gaps["locales"]:
        out = llm_manager.generate_trivia("You are a precise trivia editor. Return valid JSON only.",
                                          generation_prompt(data, language, source), response_schema=GEN_SCHEMA)
        for field in gaps["locales"].get(language, []):
            source[field] = out.get(field)
        for field in gaps["root"]:
            root[field] = out.get(field)
    check_support(data.get("correct_answer"), root, source, language)
    if not valid_order(root["option_difficulty_order"]):
        root["option_difficulty_order"] = None  # contract: null when no reliable order
    generated: Dict[str, Dict[str, Any]] = {language: source}
    others = [loc for loc in gaps["locales"] if loc != language]
    if others:
        translated, _provider = translate_localized_content(
            source, language, others, llm_manager=llm_manager, prefer_azure=False, llm_batch_size=8)
        for loc in others:
            content = dict(translated.get(loc) or {})
            if isinstance(content.get("binary_fallback"), Mapping):  # truth value comes from the validated source
                content["binary_fallback"] = {**content["binary_fallback"], "is_true": source["binary_fallback"]["is_true"]}
            generated[loc] = content
    return build_updates(gaps, root, generated)


def estimate(plans: List[Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "docs": len(plans),
        "generation_calls": sum(1 for g in plans if g["root"] or g["locales"]),
        "translation_calls": sum(math.ceil(max(0, len(g["locales"]) - 1) / 8) for g in plans),
        "locale_fields": sum(len(f) for g in plans for f in g["locales"].values()),
    }


def _gemini_env() -> None:
    os.environ.update({
        "LLM_ALLOWED_PROVIDERS": "gemini", "GEMINI_MODELS": "gemini-3.5-flash-lite,gemini-3.1-flash-lite",
        "GEMINI_QUOTA_GUARD": "1", "GEMINI_RPM": "8", "GEMINI_TPM": "200000", "GEMINI_RPD": "450",
        "GEMINI_MAX_OUTPUT_TOKENS": "65536",
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id", help="Required with --apply (must equal knowley-1).")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="Skip documents listed in the progress file.")
    args = parser.parse_args()
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(ROOT_DIR / ".env")

    import firebase_admin
    from google.cloud.firestore_v1._helpers import LastUpdateOption
    from google.cloud.firestore_v1.field_path import get_field_path

    from database.db_manager import init_firebase
    from scripts.backfill_v110_translations import _version_documents

    db = init_firebase()
    project_id = firebase_admin.get_app().project_id
    if args.apply and project_id != args.project_id:
        raise SystemExit(f"Refusing: initialized project is {project_id!r}, expected {args.project_id!r}.")
    done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if args.resume and PROGRESS_FILE.exists() else set()
    snapshots = _version_documents(db)
    todo = [(sid, snap, missing_fields(snap.to_dict() or {})) for sid, snap in sorted(snapshots.items()) if sid not in done]
    todo = [t for t in todo if t[2]["root"] or t[2]["locales"]]
    if args.limit > 0:
        todo = todo[: args.limit]
    est = estimate([t[2] for t in todo])
    logger.info("Project %s; 1.1.0 docs %d; to process %d; estimate %s; ~%.1f days at Gemini free-tier RPD 450",
                project_id, len(snapshots), len(todo), est, (est["generation_calls"] + est["translation_calls"]) / 450)
    if not args.apply:
        logger.info("Dry run only. No Firestore data was changed.")
        return 0

    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"v110_skill_backfill_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps({sid: snap.to_dict() for sid, snap, _ in todo}, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Backup written: %s", backup)
    _gemini_env()
    from core.llm_manager import AllProvidersCoolingDown, get_llm_manager

    manager = get_llm_manager()
    written = failed = 0
    done_now = set(done)
    for sid, snap, _gaps in todo:
        try:
            updates = process_doc(snap.to_dict() or {}, manager)
            if updates:
                payload = {get_field_path(list(path)): value for path, value in updates.items()}
                snap.reference.update(payload, option=LastUpdateOption(snap.update_time))
                written += 1
            done_now.add(sid)
        except AllProvidersCoolingDown as exc:
            logger.warning("Providers cooling down; stopping so the run can be resumed: %s", exc)
            break
        except Exception as exc:  # failed docs are retried by the next run
            failed += 1
            logger.warning("Skipped %s: %s", sid, str(exc)[:200])
        PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    logger.info("Updated %d docs; failed/skipped %d; progress file %s", written, failed, PROGRESS_FILE)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
