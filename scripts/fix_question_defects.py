"""Fix audited question defects. Dry-run by default; every write needs --apply --project-id knowley-1.

Modes (exactly one):
  --export-rewrites DIR      read-only: chunk_NNN.json + README_prompt.md for sub-agents (20 docs per chunk)
  --import-rewrites DIR      validate result_NNN.json and rewrite ONLY the defective option fields
  --fix-order                reorder a locale's options to the `en` positions when that is unambiguous
  --deactivate-suspects      is_active=false (+ deactivated_reason/at) for local_backups/skipped_suspect_answers.json
  --reactivate FILE          undo --deactivate-suspects from its backup file
Writes are merge-only (field paths with an update-time precondition), preceded by a local backup of the
affected documents (local_backups/, not committed), and finished documents are recorded in a progress file.
Nothing is ever deleted.
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

from scripts.audit_question_defects import LETTERS, norm

BACKUP_DIR = ROOT / "local_backups"
PROGRESS_FILE = BACKUP_DIR / "fix_defects_progress.json"
SUSPECTS_FILE = BACKUP_DIR / "skipped_suspect_answers.json"
logger = logging.getLogger("FixQuestionDefects")


# ----------------------------------------------------------------- pure detection / validation

def _has_letters(text: str) -> bool:
    return any(c.isalpha() for c in text)


def defective_options(data: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{locale: {"letters": [...rewritable defective letters], "blocked": reason|None}}.

    Duplicates keep the correct option (or the first one) and flag the rest. Number/symbol options and
    single non-ASCII characters (CJK) are legitimate and never flagged. A defective CORRECT option cannot be
    rewritten (it would change the answer) and is reported as blocked.
    """
    correct = data.get("correct_answer")
    out: Dict[str, Dict[str, Any]] = {}
    for loc, content in (data.get("translations") or {}).items():
        options = content.get("options") if isinstance(content, Mapping) else None
        if not isinstance(options, Mapping) or set(options) != set(LETTERS):
            continue
        letters: List[str] = []
        blocked: Optional[str] = None
        groups: Dict[str, List[str]] = {}
        for letter in LETTERS:
            key = norm(options.get(letter))
            tiny = not key or (len(key) == 1 and key.isascii() and not key.isdigit())
            if tiny:
                if letter == correct:
                    blocked = f"correct option {letter} is empty/tiny"
                else:
                    letters.append(letter)
            elif _has_letters(key) and not (len(key) == 1 and not key.isascii()):
                groups.setdefault(key, []).append(letter)
        for group in groups.values():
            if len(group) > 1:
                keep = correct if correct in group else group[0]
                letters.extend(l for l in group if l != keep)
                if correct in group and len([l for l in group if l != correct]) == 0:
                    blocked = blocked or "duplicate involves correct option only"
        if letters or blocked:
            out[loc] = {"letters": sorted(set(letters)), "blocked": blocked}
    return out


def rewrite_record(doc_id: str, data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    defects = {loc: info for loc, info in defective_options(data).items() if info["letters"]}
    if not defects:
        return None
    translations = data["translations"]
    return {
        "doc_id": doc_id, "correct_answer": data.get("correct_answer"),
        "locales": {
            loc: {
                "question": translations[loc].get("question"), "options": {k: translations[loc]["options"][k] for k in LETTERS},
                "explanation": translations[loc].get("explanation", ""), "defective": info["letters"],
            } for loc, info in sorted(defects.items(), key=lambda kv: (kv[0] != "en", kv[0]))
        },
    }


RESET_FIELDS = ("weakest_distractor_index", "strongest_distractor_index", "option_difficulty_order")


def validate_rewrite(data: Mapping[str, Any], result: Mapping[str, Mapping[str, str]],
                     reset_conflicting: bool = False) -> Dict[tuple, Any]:
    """Field-path -> new value. ValueError (with the reason) when anything is unsafe.

    With `reset_conflicting`, rewriting an option that weakest/strongest_distractor_index points to nulls
    the three index fields (values stay in the backup) so the backfill regenerates them."""
    defects = defective_options(data)
    correct = data.get("correct_answer")
    referenced = {data.get("weakest_distractor_index"), data.get("strongest_distractor_index")} - {None}
    updates: Dict[tuple, Any] = {}
    conflict = False
    for loc, letters_map in result.items():
        info = defects.get(loc)
        if not info or not info["letters"]:
            raise ValueError(f"{loc}: no rewritable defect")
        options = dict(data["translations"][loc]["options"])
        for letter, text in letters_map.items():
            if letter not in info["letters"] or letter == correct:
                raise ValueError(f"{loc}.{letter}: not a defective non-correct option")
            if LETTERS.index(letter) in referenced:
                if not reset_conflicting:
                    raise ValueError(f"{loc}.{letter}: referenced by weakest/strongest_distractor_index")
                conflict = True
            options[letter] = str(text or "").strip()
        if set(letters_map) != set(info["letters"]):
            raise ValueError(f"{loc}: result does not cover {info['letters']}")
        probe = {"correct_answer": correct, "translations": {loc: {"options": options}}}
        if defective_options(probe).get(loc):
            raise ValueError(f"{loc}: defect remains after rewrite")
        original = data["translations"][loc]["options"]
        for letter in letters_map:
            updates[("translations", loc, "options", letter)] = options[letter]
        if options[correct] != original[correct]:
            raise ValueError(f"{loc}: correct option changed")
    if conflict:
        updates.update({(field,): None for field in RESET_FIELDS})
    return updates


def plan_reorder(data: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Locales whose options sit in other positions than `en`: {loc: {"options": new|None, "reason": ...}}.

    Only reordered automatically when every option text of the locale also exists in `en` (names/numbers),
    which makes the permutation unambiguous; anything else is reported as uncertain.
    """
    translations = data.get("translations") or {}
    base = (translations.get("en") or {}).get("options")
    if not isinstance(base, Mapping):
        return {}
    en_pos: Dict[str, List[str]] = {}
    for letter in LETTERS:
        en_pos.setdefault(norm(base.get(letter)), []).append(letter)
    out: Dict[str, Dict[str, Any]] = {}
    for loc, content in translations.items():
        options = content.get("options") if isinstance(content, Mapping) else None
        if loc == "en" or not isinstance(options, Mapping) or set(options) != set(LETTERS):
            continue
        keys = [norm(options[l]) for l in LETTERS]
        mismatch = [l for l, k in zip(LETTERS, keys) if len(k) > 1 and k in en_pos and l not in en_pos[k]]
        if not mismatch:
            continue
        if all(len(en_pos.get(k, [])) == 1 for k in keys) and len({en_pos[k][0] for k in keys}) == 4:
            out[loc] = {"options": {en_pos[k][0]: options[l] for l, k in zip(LETTERS, keys)}, "reason": "all options present in en"}
        else:
            out[loc] = {"options": None, "reason": "uncertain: not every option matches an en option"}
    return out


def deactivation_update(now: Any) -> Dict[str, Any]:
    return {"is_active": False, "deactivated_reason": "suspect_answer_key", "deactivated_at": now}


def summarize(states: Mapping[str, str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for state in states.values():
        key = "skipped" if state.startswith("skipped") else state
        counts[key] = counts.get(key, 0) + 1
    return counts


PROMPT_README = """# Task for the sub-agent

`chunk_NNN.json` lists questions with defective answer options (duplicate or empty/too short).
Each record: `doc_id`, `correct_answer` and `locales` -> `{locale: {question, options, explanation, defective}}`.

For every locale of every record, rewrite ONLY the letters listed in `defective`:
- NEVER touch the correct option (`correct_answer`) or any option that is not listed.
- The new text must be written in the SAME language and format/length/style as the other options of that locale.
- It must be a plausible but WRONG distractor for that question: different from every other option of the locale
  (no duplicates, no synonyms of the correct answer) and not a fact that would also make the question correct.
- Do not add explanations. Do not translate from another locale; write each locale in its own language.
- Do the `en` locale first and carefully; the other locales follow the same idea in their own language.

Save `result_NNN.json` (same NNN) as one JSON object: `{ "<doc_id>": { "<locale>": { "<LETTER>": "<new text>" } } }`
with exactly the defective letters per locale. Output valid JSON only. Records you cannot do reliably may be omitted.
"""


def write_chunks(records: List[Mapping[str, Any]], directory: Path, size: int) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for start in range(0, len(records), size):
        count += 1
        (directory / f"chunk_{count:03d}.json").write_text(json.dumps(records[start:start + size], ensure_ascii=False, indent=1), encoding="utf-8")
    (directory / "README_prompt.md").write_text(PROMPT_README, encoding="utf-8")
    return count


def read_results(directory: Path) -> Dict[str, Mapping[str, Any]]:
    results: Dict[str, Mapping[str, Any]] = {}
    for path in sorted(directory.glob("result_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: expected an object keyed by doc_id")
        results.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
    return results


# ----------------------------------------------------------------- Firestore side

def _progress() -> set:
    return set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))) if PROGRESS_FILE.exists() else set()


def _commit(mode: str, plan: List[tuple], apply: bool, states: Dict[str, str], ok_label: str) -> None:
    """plan = [(doc_id, snapshot, payload)]. Backs up, writes, records progress; updates `states`."""
    from google.cloud.firestore_v1._helpers import LastUpdateOption
    from google.cloud.firestore_v1.field_path import get_field_path

    for doc_id, _snap, _payload in plan:
        states[doc_id] = ok_label if apply else "valid (dry-run)"
    if not apply or not plan:
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"fix_{mode}_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(json.dumps({d: s.to_dict() for d, s, _ in plan}, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("Backup written: %s", backup)
    done = _progress()
    for doc_id, snap, payload in plan:
        try:
            fields = {(k if isinstance(k, str) else get_field_path(list(k))): v for k, v in payload.items()}
            snap.reference.update(fields, option=LastUpdateOption(snap.update_time))
            done.add(f"{mode}:{doc_id}")
        except Exception as exc:
            states[doc_id] = f"skipped: write failed ({type(exc).__name__}: {str(exc)[:100]})"
    PROGRESS_FILE.write_text(json.dumps(sorted(done)), encoding="utf-8")


def _report(label: str, states: Mapping[str, str], apply: bool) -> None:
    for doc_id, state in states.items():
        if state.startswith("skipped"):
            logger.warning("%s -> %s", doc_id, state)
    logger.info("%s: %s%s", label, summarize(states), "" if apply else " (dry-run, nothing written)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--export-rewrites", metavar="DIR")
    parser.add_argument("--import-rewrites", metavar="DIR")
    parser.add_argument("--fix-order", action="store_true")
    parser.add_argument("--deactivate-suspects", action="store_true")
    parser.add_argument("--reactivate", metavar="BACKUP_FILE")
    parser.add_argument("--reset-conflicting-indices", action="store_true",
                        help="With --import-rewrites: null weakest/strongest/option_difficulty_order instead of skipping.")
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    modes = [bool(args.export_rewrites), bool(args.import_rewrites), args.fix_order, args.deactivate_suspects, bool(args.reactivate)]
    if sum(modes) != 1:
        parser.error("choose exactly one mode")
    if args.apply and args.project_id != "knowley-1":
        parser.error("--apply requires --project-id knowley-1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import firebase_admin
    from firebase_admin import firestore

    from database.db_manager import init_firebase

    db = init_firebase()
    if args.apply and firebase_admin.get_app().project_id != args.project_id:
        raise SystemExit("Refusing: initialized Firebase project differs from --project-id.")
    done = _progress() if args.resume else set()
    states: Dict[str, str] = {}

    if args.export_rewrites:
        records = []
        for snap in db.collection("questions").stream():
            record = rewrite_record(snap.id, snap.to_dict() or {})
            if record:
                records.append(record)
        records.sort(key=lambda r: ("en" not in r["locales"], r["doc_id"]))
        chunks = write_chunks(records, Path(args.export_rewrites), max(1, args.chunk_size))
        logger.info("Exported %d docs into %d chunk(s) in %s", len(records), chunks, args.export_rewrites)
        return 0

    if args.import_rewrites:
        plan = []
        for doc_id, result in sorted(read_results(Path(args.import_rewrites)).items()):
            if f"rewrite:{doc_id}" in done:
                states[doc_id] = "skipped: already done"
                continue
            snap = db.collection("questions").document(doc_id).get()
            if not snap.exists:
                states[doc_id] = "skipped: document not found"
                continue
            try:
                plan.append((doc_id, snap, validate_rewrite(snap.to_dict() or {}, result, args.reset_conflicting_indices)))
            except ValueError as exc:
                states[doc_id] = f"skipped: invalid ({str(exc)[:140]})"
        resets = [d for d, _s, payload in plan if ("weakest_distractor_index",) in payload]
        logger.info("%d doc(s) %s have their index fields reset to null", len(resets), "will" if not args.apply else "had")
        _commit("rewrite", plan, args.apply, states, "written")
        if args.apply and resets:  # let the skill-field backfill see them again
            backfill = ROOT / "local_backups" / "v110_skill_backfill_progress.json"
            if backfill.exists():
                ids = set(json.loads(backfill.read_text(encoding="utf-8")))
                ids -= {d for d in resets if states.get(d) == "written"}
                backfill.write_text(json.dumps(sorted(ids)), encoding="utf-8")
        _report("Rewrite import", states, args.apply)
        return 0

    if args.fix_order:
        plan = []
        for snap in db.collection("questions").stream():
            moves = plan_reorder(snap.to_dict() or {})
            if not moves or f"order:{snap.id}" in done:
                continue
            unsure = {loc: m["reason"] for loc, m in moves.items() if m["options"] is None}
            if unsure:
                states[snap.id] = f"skipped: {unsure}"
                continue
            payload = {("translations", loc, "options", l): t for loc, m in moves.items() for l, t in m["options"].items()}
            logger.info("%s reorder %s", snap.id[:12], {loc: m["options"] for loc, m in moves.items()})
            plan.append((snap.id, snap, payload))
        _commit("order", plan, args.apply, states, "written")
        _report("Fix order", states, args.apply)
        return 0

    if args.deactivate_suspects:
        suspects = [r["doc_id"] for r in json.loads(SUSPECTS_FILE.read_text(encoding="utf-8"))]
        plan = []
        for doc_id in suspects:
            snap = db.collection("questions").document(doc_id).get()
            if not snap.exists:
                states[doc_id] = "skipped: document not found"
            elif (snap.to_dict() or {}).get("is_active") is False or f"deactivate:{doc_id}" in done:
                states[doc_id] = "skipped: already inactive"
            else:
                plan.append((doc_id, snap, deactivation_update(firestore.SERVER_TIMESTAMP)))
        _commit("deactivate", plan, args.apply, states, "deactivated")
        _report("Deactivate suspects", states, args.apply)
        return 0

    backup = json.loads(Path(args.reactivate).read_text(encoding="utf-8"))
    plan = []
    for doc_id, old in backup.items():
        snap = db.collection("questions").document(doc_id).get()
        if not snap.exists:
            states[doc_id] = "skipped: document not found"
            continue
        payload = {"is_active": old.get("is_active", True), "deactivated_reason": firestore.DELETE_FIELD,
                   "deactivated_at": firestore.DELETE_FIELD}
        plan.append((doc_id, snap, payload))
    _commit("reactivate", plan, args.apply, states, "reactivated")
    _report("Reactivate", states, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
