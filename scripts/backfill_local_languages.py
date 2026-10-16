"""Complete missing country languages (+ en) on active LOCAL questions via sub-agent translations.

  --export-missing DIR [--chunk-size 15]   read-only: chunk_NNN.json + README_prompt.md
  --import-results DIR [--apply --project-id knowley-1] [--resume]
                                           validate result_NNN.json; with --apply write merge-only

Existing translations are never changed or removed: only missing `translations.<lang>.<field>` paths are
written, and the language is ADDED to supported_languages (never removed). Writes are preceded by a local
backup (local_backups/, not committed); every document gets a written/skipped+reason line.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_local_languages import canon, missing_languages, resolve_country, usable

BACKUP_DIR = ROOT / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "local_languages_progress.json"
TEXT_FIELDS = ("question", "explanation", "filter_tag", "hint", "keyword", "distractor_trap_note", "simplified_question")
logger = logging.getLogger("BackfillLocalLanguages")


def _text(value: Any) -> bool:
    return bool(str(value or "").strip())


def pick_source(data: Mapping[str, Any]) -> Optional[str]:
    translations = data.get("translations") or {}
    for loc in ("en", *translations):
        if usable(translations.get(loc)):
            return loc
    return None


def export_record(doc_id: str, data: Mapping[str, Any], country: str, missing: List[str]) -> Optional[Dict[str, Any]]:
    source = pick_source(data)
    if source is None or not missing:
        return None
    content = data["translations"][source]
    keep = [*TEXT_FIELDS, "options", "clarified_options", "binary_fallback"]
    return {"doc_id": doc_id, "country": country, "source_language": source, "correct_answer": data.get("correct_answer"),
            "missing_languages": missing, "source": {k: content.get(k) for k in keep if content.get(k) not in (None, "")}}


README = """# Task for the sub-agent

`chunk_NNN.json` lists local questions that lack some of their country's languages. Each record has `doc_id`,
`source_language`, `source` (the full content) and `missing_languages` (exact locale codes, e.g. `fr-FR`, `ja-JP`, `en`).

For EVERY code in `missing_languages` translate the whole `source` into that language:
`question`, `options` {A,B,C,D}, `explanation`, `filter_tag`, and when present in the source `hint`, `keyword`,
`distractor_trap_note`, `binary_fallback` {statement, is_true}, `simplified_question`, `clarified_options` {A,B,C,D}.
- Keep option keys A-D in the same order and meaning; the correct answer letter does not change; options must stay distinct.
- `binary_fallback.is_true` is copied unchanged from the source.
- Keep numbers, names and formatting; do not add facts.

Save `result_NNN.json` (same NNN): `{ "<doc_id>": { "<locale code exactly as listed>": { ...translated fields... } } }`.
Valid JSON only. Records you cannot do reliably may be omitted.
"""


def validate_language(source: Mapping[str, Any], content: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the cleaned translation or raise ValueError."""
    out: Dict[str, Any] = {}
    for field in TEXT_FIELDS:
        if _text(source.get(field)):
            if not _text(content.get(field)):
                if field == "filter_tag":  # tag may legitimately stay as in the source
                    out[field] = source[field]
                    continue
                raise ValueError(f"missing {field}")
            out[field] = str(content[field]).strip()
    for field in ("options", "clarified_options"):
        if isinstance(source.get(field), Mapping):
            value = content.get(field)
            if not isinstance(value, Mapping) or set(value) != set("ABCD") or not all(_text(value[k]) for k in "ABCD"):
                raise ValueError(f"{field} must have non-empty A-D")
            if len({str(value[k]).strip().casefold() for k in "ABCD"}) != 4:
                raise ValueError(f"{field} options are not distinct")
            out[field] = {k: str(value[k]).strip() for k in "ABCD"}
    fallback = source.get("binary_fallback")
    if isinstance(fallback, Mapping):
        value = content.get("binary_fallback")
        if not isinstance(value, Mapping) or not _text(value.get("statement")) or value.get("is_true") is not fallback.get("is_true"):
            raise ValueError("binary_fallback needs a statement and the same is_true as the source")
        out["binary_fallback"] = {"statement": str(value["statement"]).strip(), "is_true": fallback["is_true"]}
    return out


def plan_update(data: Mapping[str, Any], missing: List[str], result: Mapping[str, Mapping[str, Any]]) -> Dict[tuple, Any]:
    """Field-path -> value for the missing languages in `result`; ValueError when anything is invalid."""
    source_lang = pick_source(data)
    if source_lang is None:
        raise ValueError("no usable source translation")
    source = data["translations"][source_lang]
    updates: Dict[tuple, Any] = {}
    added: List[str] = []
    for lang, content in result.items():
        if lang not in missing:
            raise ValueError(f"{lang}: not a missing language of this question")
        cleaned = validate_language(source, content if isinstance(content, Mapping) else {})
        existing = (data.get("translations") or {}).get(lang) or {}
        for field, value in cleaned.items():
            leaves = {(field, k): v for k, v in value.items()} if isinstance(value, Mapping) else {(field,): value}
            for path, leaf in leaves.items():
                current = existing
                for key in path:
                    current = current.get(key) if isinstance(current, Mapping) else None
                if current is None or (isinstance(current, str) and not current.strip()):  # is_true False is a real value
                    updates[("translations", lang, *path)] = leaf
        added.append(lang)
    supported = list(data.get("supported_languages") or [])
    new_supported = supported + [l for l in added if canon(l) not in {canon(x) for x in supported}]
    if new_supported != supported:
        updates[("supported_languages",)] = new_supported
    return updates


def summarize(states: Mapping[str, str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for state in states.values():
        key = "skipped" if state.startswith("skipped") else state
        counts[key] = counts.get(key, 0) + 1
    return counts


def read_results(directory: Path) -> Dict[str, Mapping[str, Any]]:
    results: Dict[str, Mapping[str, Any]] = {}
    for path in sorted(directory.glob("result_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: expected an object keyed by doc_id")
        results.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
    return results


def scan(db: Any):
    """Yield (snapshot, data, country_row, missing) for active local questions that miss required languages."""
    from core.sheet_algorithm import get_sheet_algorithm_repository
    from scripts.backfill_v110_translations import _country_for_question

    repository = get_sheet_algorithm_repository()
    for snap in db.collection("questions").stream():
        data = snap.to_dict() or {}
        if str(data.get("scope") or "").lower() != "local" or data.get("is_active") is False:
            continue
        country = resolve_country(data, repository, _country_for_question)
        if country is None:
            continue
        missing = missing_languages(data, repository.question_languages(country["name"]))
        if missing:
            yield snap, data, country, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-missing", metavar="DIR")
    parser.add_argument("--import-results", metavar="DIR")
    parser.add_argument("--chunk-size", type=int, default=15)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if bool(args.export_missing) == bool(args.import_results):
        parser.error("choose exactly one of --export-missing / --import-results")
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import firebase_admin
    from google.cloud.firestore_v1._helpers import LastUpdateOption
    from google.cloud.firestore_v1.field_path import get_field_path

    from database.db_manager import init_firebase

    db = init_firebase()
    if args.apply and firebase_admin.get_app().project_id != args.project_id:
        raise SystemExit("Refusing: initialized Firebase project differs from --project-id.")

    if args.export_missing:
        records = [r for snap, data, country, missing in scan(db)
                   if (r := export_record(snap.id, data, country["name"], missing))]
        directory = Path(args.export_missing)
        directory.mkdir(parents=True, exist_ok=True)
        size = max(1, args.chunk_size)
        chunks = 0
        for start in range(0, len(records), size):
            chunks += 1
            (directory / f"chunk_{chunks:03d}.json").write_text(json.dumps(records[start:start + size], ensure_ascii=False, indent=1), encoding="utf-8")
        (directory / "README_prompt.md").write_text(README, encoding="utf-8")
        logger.info("Exported %d docs into %d chunk(s) in %s", len(records), chunks, directory)
        return 0

    done = set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if args.resume and PROGRESS_FILE.exists() else set()
    states: Dict[str, str] = {}
    plan = []
    gaps = {snap.id: (snap, data, missing) for snap, data, _c, missing in scan(db)}
    for doc_id, result in sorted(read_results(Path(args.import_results)).items()):
        if doc_id in done:
            states[doc_id] = "skipped: already done"
        elif doc_id not in gaps:
            states[doc_id] = "skipped: no longer has missing languages"
        else:
            snap, data, missing = gaps[doc_id]
            try:
                plan.append((doc_id, snap, data, plan_update(data, missing, result)))
                states[doc_id] = "written" if args.apply else "valid (dry-run)"
            except ValueError as exc:
                states[doc_id] = f"skipped: invalid ({str(exc)[:140]})"
    if args.apply and plan:
        BACKUP_DIR.mkdir(exist_ok=True)
        backup = BACKUP_DIR / f"local_languages_{datetime.now():%Y%m%d_%H%M%S}.json"
        backup.write_text(json.dumps({d: data for d, _s, data, _u in plan}, ensure_ascii=False, default=str), encoding="utf-8")
        logger.info("Backup written: %s", backup)
        done_now = set(done)
        for doc_id, snap, _data, updates in plan:
            try:
                fields = {get_field_path(list(path)): value for path, value in updates.items()}
                snap.reference.update(fields, option=LastUpdateOption(snap.update_time))
                done_now.add(doc_id)
            except Exception as exc:
                states[doc_id] = f"skipped: write failed ({type(exc).__name__}: {str(exc)[:100]})"
        PROGRESS_FILE.write_text(json.dumps(sorted(done_now)), encoding="utf-8")
    for doc_id, state in states.items():
        if state.startswith("skipped"):
            logger.warning("%s -> %s", doc_id, state)
    logger.info("Import: %s%s", summarize(states), "" if args.apply else " (dry-run, nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
