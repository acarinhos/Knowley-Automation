"""Read-only defect audit of the whole `questions` collection (no writes).

Run: python -m scripts.audit_question_defects
Prints counts per defect type with 5 examples each, writes the full list to
local_backups/defects_report.json and the "skipped by the sub-agents" list (chunk ids without a
result) to local_backups/skipped_suspect_answers.json.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "local_backups"
LETTERS = "ABCD"
TERMINAL = tuple('.!?…"\'”’)»。！？')
DANGLING = {"and", "or", "the", "of", "in", "to", "a", "an", "with", "by", "for", "is", "was", "as", "that", "which", "because"}


def norm(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", text).strip()


def _locales(data: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {k: v for k, v in (data.get("translations") or {}).items() if isinstance(v, Mapping)}


def detect(data: Mapping[str, Any]) -> List[Dict[str, str]]:
    """All defects of one document as [{"type": ..., "detail": ...}]."""
    found: List[Dict[str, str]] = []

    def add(kind: str, detail: str) -> None:
        found.append({"type": kind, "detail": detail[:160]})

    locales = _locales(data)
    for loc, content in locales.items():
        options = content.get("options")
        if not isinstance(options, Mapping):
            add("bad_option_keys", f"{loc}: options missing")
            continue
        if set(options) != set(LETTERS):
            add("bad_option_keys", f"{loc}: keys {sorted(options)}")
        seen: Dict[str, str] = {}
        for letter in LETTERS:
            text = str(options.get(letter) or "")
            if not norm(text) or (len(norm(text)) == 1 and norm(text).isascii() and not norm(text).isdigit()):
                add("empty_or_tiny_option", f"{loc}.{letter}={text!r}")
            key = norm(text)
            if key and key in seen:
                add("duplicate_options", f"{loc}: {seen[key]}/{letter}={text!r}")
            seen.setdefault(key, letter)
        question = str(content.get("question") or "")
        if len(question.strip()) < (4 if any(ord(c) > 0x2E7F for c in question) else 10):  # CJK is dense
            add("empty_or_tiny_question", f"{loc}: {question!r}")

    answer, option = data.get("correct_answer"), data.get("correct_option")
    if answer not in tuple(LETTERS) or (option is not None and option not in tuple(LETTERS)):
        add("bad_correct_answer", f"correct_answer={answer!r} correct_option={option!r}")
    elif option is not None and option != answer:
        add("bad_correct_answer", f"correct_answer={answer!r} != correct_option={option!r}")

    index = LETTERS.index(answer) if answer in tuple(LETTERS) else None
    weakest, strongest = data.get("weakest_distractor_index"), data.get("strongest_distractor_index")
    for name, value in (("weakest", weakest), ("strongest", strongest)):
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 3 or value == index:
            add("bad_distractor_index", f"{name}={value!r} correct_index={index}")
    if weakest is not None and weakest == strongest:
        add("bad_distractor_index", f"weakest == strongest == {weakest!r}")

    base = locales.get("en")
    if base and isinstance(base.get("options"), Mapping):
        base_pos: Dict[str, List[str]] = defaultdict(list)
        for letter in LETTERS:
            base_pos[norm(base["options"].get(letter))].append(letter)
        for loc, content in locales.items():
            if loc == "en" or not isinstance(content.get("options"), Mapping):
                continue
            moved = [
                (letter, base_pos[norm(content["options"].get(letter))])
                for letter in LETTERS
                if len(norm(content["options"].get(letter))) > 1
                and norm(content["options"].get(letter)) in base_pos
                and letter not in base_pos[norm(content["options"].get(letter))]
            ]
            if moved:
                add("locale_option_order_mismatch", f"{loc}: {moved[:2]} differs from en positions")

    explanation = str((base or {}).get("explanation") or "").strip()
    if base is not None:
        words = explanation.split()
        if len(explanation) < 10 or (not explanation.endswith(TERMINAL) and (words[-1].casefold() in DANGLING or explanation[-1] in ",;:-–(")):
            add("incomplete_explanation", repr(explanation[-60:]))
    return found


def suspect_list(directory: Path) -> List[Dict[str, Any]]:
    """Chunk records that got no sub-agent result (answer key looked suspicious)."""
    done = set()
    for path in sorted(directory.glob("result_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        done.update(data if isinstance(data, dict) else [])
    rows = []
    for path in sorted(directory.glob("chunk_*.json")):
        for rec in json.loads(path.read_text(encoding="utf-8")):
            if rec["doc_id"] not in done:
                letter = rec.get("correct_answer")
                rows.append({
                    "doc_id": rec["doc_id"], "question": rec.get("question"), "correct_option": letter,
                    "correct_text": (rec.get("options") or {}).get(letter), "explanation": rec.get("explanation"),
                })
    return rows


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from database.db_manager import init_firebase

    db = init_firebase()
    by_type: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    total = 0
    versions: Dict[str, int] = defaultdict(int)
    for snap in db.collection("questions").stream():
        data = snap.to_dict() or {}
        total += 1
        versions[str(data.get("dbVersion") or data.get("version") or "?")] += 1
        for defect in detect(data):
            by_type[defect["type"]].append({"doc_id": snap.id, "version": str(data.get("dbVersion") or data.get("version") or "?"),
                                            "active": data.get("is_active"), **defect})
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "defects_report.json").write_text(json.dumps(by_type, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Scanned {total} questions; versions: {dict(versions)}")
    for kind, rows in sorted(by_type.items()):
        print(f"\n{kind}: {len(rows)} defect(s) in {len({r['doc_id'] for r in rows})} doc(s)")
        for row in rows[:5]:
            print(f"  {row['doc_id'][:12]} v{row['version']} {row['detail']}")
    pending = ROOT / "local_backups" / "pending_chunks"
    if pending.exists():
        skipped = suspect_list(pending)
        (OUT_DIR / "skipped_suspect_answers.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nChunk docs without a sub-agent result: {len(skipped)} -> local_backups/skipped_suspect_answers.json")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
