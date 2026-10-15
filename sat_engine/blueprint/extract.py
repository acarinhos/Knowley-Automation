"""Extract abstract question blueprints from reference pages (LLM + verbatim guard)."""
from __future__ import annotations

import argparse
import difflib
import json
from collections import Counter
from pathlib import Path

from ..ingest.extract import iter_pages
from ..llm import generate_json
from ..schema import TAXONOMY
from .guard import blueprint_texts, copies_source
from .model import VISUAL_KINDS, Blueprint

OUT = Path(__file__).resolve().parents[1] / "blueprints" / "blueprints.json"

SYSTEM = (
    "You analyse digital-SAT practice pages and describe ABSTRACT question patterns only. "
    "Never quote or closely paraphrase passages, stems or options; include no names and no numbers from the source. "
    "Describe what skill is tested, the reasoning steps in general terms, and the kinds of wrong answers. "
    "Return JSON: {\"blueprints\": [...]} with one item per question on the page (empty list if none).")


def _prompt(text: str) -> str:
    return (
        f"Taxonomy (section -> domain -> skills): {json.dumps(TAXONOMY)}\n"
        "Each item needs: section (rw|math), domain, skill (exact taxonomy skill), difficulty (easy|medium|hard), "
        "answer_type (mcq|grid_in), passage_genre (optional, generic like 'science passage'), "
        "stem_pattern (<=200 chars, abstract), reasoning_steps (list of short generic strings), "
        "distractor_types (list of generic strings), needs_visual (bool), "
        f"visual_kind (optional, one of {VISUAL_KINDS}).\n\nPAGE TEXT:\n{text}")


def map_skill(section, skill):
    """Fuzzy-map to (domain, skill) in TAXONOMY, or None."""
    if section not in TAXONOMY:
        return None
    names = {s.lower(): (d, s) for d, ss in TAXONOMY[section].items() for s in ss}
    m = difflib.get_close_matches(str(skill).lower(), list(names), n=1, cutoff=0.6)
    return names[m[0]] if m else None


def page_blueprints(source, page, text, llm=generate_json):
    """Returns (accepted blueprints, dropped count) for one page."""
    data = llm(_prompt(text), system=SYSTEM)
    items = data.get("blueprints", []) if isinstance(data, dict) else data
    out, dropped = [], 0
    for i, it in enumerate(items or [], 1):
        try:
            ds = map_skill(it.get("section"), it.get("skill"))
            if not ds:
                raise ValueError("skill")
            it = {**it, "domain": ds[0], "skill": ds[1], "id": f"{source}-p{page}-{i}",
                  "source_tag": source, "page": page}
            if it.get("visual_kind") not in VISUAL_KINDS:
                it["visual_kind"] = None
            bp = Blueprint.model_validate(it)
            if copies_source(blueprint_texts(bp), text):
                raise ValueError("verbatim")
            out.append(bp)
        except Exception:  # noqa: BLE001 - drop any malformed/unsafe item
            dropped += 1
    return out, dropped


def extract_blueprints(source, pages=None, llm=generate_json):
    """pages: iterable of page numbers (None = all). Returns list[Blueprint]."""
    want = set(pages) if pages else None
    res = []
    for p, text in iter_pages(source):
        if want is not None and p not in want:
            continue
        if len(text.strip()) < 100:
            continue
        try:
            bps, dropped = page_blueprints(source, p, text, llm)
        except Exception as e:  # noqa: BLE001
            print(f"{source} p{p}: LLM failed: {type(e).__name__}: {str(e)[:150]}")
            continue
        print(f"{source} p{p}: kept={len(bps)} dropped={dropped}")
        res.extend(bps)
    return res


def load_blueprints(path=OUT) -> list[Blueprint]:
    path = Path(path)
    if not path.exists():
        return []
    return [Blueprint.model_validate(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def merge_save(new, path=OUT):
    by_id = {b.id: b for b in load_blueprints(path)}
    by_id.update({b.id: b for b in new})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([b.model_dump() for b in by_id.values()], indent=1, ensure_ascii=False), encoding="utf-8")
    return len(by_id)


def coverage(bps=None) -> dict:
    bps = load_blueprints() if bps is None else bps
    return {k: dict(Counter(getattr(b, k) for b in bps)) for k in ("domain", "skill", "difficulty")}


def parse_pages(s):
    if not s:
        return None
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=["march2021", "sample4"])
    ap.add_argument("--pages")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    new = extract_blueprints(a.source, parse_pages(a.pages))
    print(f"new={len(new)} total={merge_save(new, a.out)}")


if __name__ == "__main__":
    main()
