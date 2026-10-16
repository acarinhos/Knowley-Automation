"""Extract abstract question blueprints from reference pages (LLM + verbatim guard)."""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..ingest.extract import iter_pages
from ..llm import generate_json
from ..schema import TAXONOMY
from .guard import blueprint_texts, copies_source
from .model import VISUAL_KINDS, Blueprint

OUT = Path(__file__).resolve().parents[1] / "blueprints" / "blueprints.json"
STATE = OUT.parent / ".state.json"
PROVIDER_ORDER = ["groq", "cohere", "nvidia"]
REASONS = ("skill_unmatched", "guard_verbatim", "invalid_schema", "llm_error", "empty", "low_text")
log = logging.getLogger("sat_engine.blueprint")
_lock = threading.Lock()

SYSTEM = (
    "You analyse digital-SAT practice pages and describe ABSTRACT question patterns only. "
    "Never quote or closely paraphrase passages, stems or options; include no names and no numbers from the source. "
    "Describe what skill is tested, the reasoning steps in general terms, and the kinds of wrong answers. "
    "Return JSON: {\"blueprints\": [...]} with one item per question on the page (empty list if none).")

MATH_GUIDE = (
    "MATH pages: the source may be the older paper SAT (no-calculator / calculator split, student-produced "
    "grid-in responses): map every question to the digital SAT domain/skill taxonomy anyway. For each question "
    "capture the abstract problem structure in stem_pattern (e.g. 'given a linear relationship in words, find a "
    "parameter'), the required concept in reasoning_steps, typical wrong-answer traps in distractor_types "
    "(sign error, forgot to distribute, unit or percent confusion, solving for the wrong quantity, off-by-one, "
    "etc.), answer_type grid_in for student-produced responses, and needs_visual true with the best visual_kind "
    "when a graph, table, figure or diagram is required.")
RW_GUIDE = "READING/WRITING pages: also give passage_genre and the rhetorical or logical move the question tests."
VISUAL_NOTE = ("Some page text is partial because a figure was not read: set needs_visual true and give your "
               "best-effort visual_kind for questions that clearly depend on a figure.")

KEYWORDS = {
    "rw": {
        "Words in Context": ["word", "phrase", "vocabulary", "context", "meaning"],
        "Text Structure and Purpose": ["purpose", "structure", "function", "underlined"],
        "Cross-Text Connections": ["cross", "text 1", "two texts", "both texts", "respond"],
        "Central Ideas and Details": ["central", "main idea", "detail", "summar"],
        "Command of Evidence": ["evidence", "support", "claim", "data", "quantitative", "textual"],
        "Inferences": ["infer", "logical", "complete the text", "conclusion"],
        "Boundaries": ["boundar", "punctuation", "comma", "semicolon", "colon"],
        "Form, Structure, and Sense": ["form", "verb", "agreement", "pronoun", "tense", "grammar", "modifier"],
        "Rhetorical Synthesis": ["synthes", "notes", "goal", "rhetorical"],
        "Transitions": ["transition", "logical connect", "conjunct"],
    },
    "math": {
        "Linear equations in one variable": ["one variable", "solve for", "linear equation"],
        "Linear functions": ["linear function", "slope", "intercept", "rate of change"],
        "Linear equations in two variables": ["two variables", "line graph", "linear relationship"],
        "Systems of two linear equations in two variables": ["system"],
        "Linear inequalities in one or two variables": ["inequalit"],
        "Equivalent expressions": ["expression", "factor", "simplif", "expand", "equivalent"],
        "Nonlinear equations in one variable and systems of equations in two variables":
            ["quadratic", "nonlinear equation", "radical", "rational", "exponent"],
        "Nonlinear functions": ["nonlinear function", "parabola", "exponential", "polynomial", "function"],
        "Ratios, rates, proportional relationships, and units": ["ratio", "rate", "proportion", "unit"],
        "Percentages": ["percent"],
        "One-variable data: distributions and measures of center and spread":
            ["mean", "median", "standard deviation", "spread", "distribution", "histogram", "dot plot"],
        "Two-variable data: models and scatterplots": ["scatter", "line of best fit", "two-variable", "regression"],
        "Probability and conditional probability": ["probab", "conditional", "random"],
        "Inference from sample statistics and margin of error": ["margin of error", "sample", "inference", "survey"],
        "Evaluating statistical claims: observational studies and experiments":
            ["observational", "experiment", "causal", "random assignment", "generaliz"],
        "Area and volume": ["area", "volume", "perimeter", "prism", "cylinder", "cone", "sphere"],
        "Lines, angles, and triangles": ["angle", "triangle", "parallel", "congruen", "similar", "line segment"],
        "Right triangles and trigonometry": ["right triangle", "pythag", "sine", "cosine", "tangent", "trig"],
        "Circles": ["circle", "arc", "sector", "radius", "chord"],
    },
}


def default_llm(prompt, system=None):
    return generate_json(prompt, system=system, providers=PROVIDER_ORDER)


def looks_math(t: str) -> bool:
    """Heuristic Math-page detector (equations, grid-in/calculator markers, number-heavy text)."""
    l = t.lower()
    s = sum(k in l for k in ("student-produced", "grid", "calculator", "math test", "f(x)", "equation", "slope",
                              "triangle", "y =", "x =", "integer"))
    s += t.count("=") >= 4
    s += sum(c.isdigit() for c in t) / max(len(t), 1) > 0.08
    return s >= 2


def _prompt(text: str, math: bool, mixed=False, partial=False) -> str:
    guide = f"{MATH_GUIDE} {RW_GUIDE}" if mixed else (MATH_GUIDE if math else RW_GUIDE)
    return (
        f"Taxonomy (section -> domain -> skills): {json.dumps(TAXONOMY)}\n"
        "Each item needs: page (number from the === PAGE n === marker), section (rw|math), domain, skill "
        "(exact taxonomy skill), difficulty (easy|medium|hard), answer_type (mcq|grid_in), passage_genre (optional, "
        "generic like 'science passage'), stem_pattern (<=200 chars, abstract), reasoning_steps (list of short "
        "generic strings), distractor_types (list of generic strings), needs_visual (bool), "
        f"visual_kind (optional, one of {VISUAL_KINDS}).\n{guide}{' ' + VISUAL_NOTE if partial else ''}\n\n"
        f"PAGE TEXT:\n{text}")


def map_skill(section, skill, hint=None, llm=None):
    """Map to (domain, skill) in TAXONOMY: fuzzy, then keywords, then LLM restricted to the section's skills."""
    if section not in TAXONOMY:
        return None
    names = {s.lower(): (d, s) for d, ss in TAXONOMY[section].items() for s in ss}
    m = difflib.get_close_matches(str(skill).lower(), list(names), n=1, cutoff=0.6)
    if m:
        return names[m[0]]
    low = f"{skill} {hint or ''}".lower()
    score = {k: sum(w in low for w in ws) for k, ws in KEYWORDS[section].items()}
    best = max(score, key=score.get)
    if score[best] and sorted(score.values())[-2] < score[best]:
        return names[best.lower()]
    if llm:
        try:
            r = llm(f"Section {section}. A question was described as: {skill!r} / {hint!r}. Choose the single best "
                    f"skill from this list: {[v[1] for v in names.values()]}. "
                    "Return JSON {\"skill\": \"<exact skill>\"}.", system="You classify SAT skills.")
            m = difflib.get_close_matches(str((r or {}).get("skill", "")).lower(), list(names), n=1, cutoff=0.8)
            if m:
                return names[m[0]]
        except Exception:  # noqa: BLE001
            pass
    return names[best.lower()] if score[best] else None


def _reword(it, llm):
    keys = ("stem_pattern", "passage_genre", "reasoning_steps", "distractor_types")
    r = llm("These question descriptions overlap too closely with their source text. Re-describe them MORE "
            "ABSTRACTLY in different general words (no specific nouns, names, numbers or phrases). Same JSON keys: "
            f"{json.dumps({k: it.get(k) for k in keys})}", system=SYSTEM)
    return {**it, **{k: r[k] for k in keys if isinstance(r, dict) and k in r}}


def page_blueprints(source, page, text, llm=default_llm, math=None, pages=None, partial=False):
    """Returns (accepted blueprints, drops Counter) for a page or page chunk (`pages` = its page numbers)."""
    drops, pages = Counter(), pages or [page]
    math = looks_math(text) if math is None else math
    try:
        data = llm(_prompt(text, math, mixed=len(pages) > 1, partial=partial), system=SYSTEM)
    except Exception as e:  # noqa: BLE001
        drops["llm_error"] += 1
        log.info("drop %s p%s reason=llm_error err=%s", source, pages, type(e).__name__)
        return [], drops
    items = data.get("blueprints", []) if isinstance(data, dict) else data
    if not items:
        drops["empty"] += 1
        log.info("drop %s p%s reason=empty", source, pages)
        return [], drops
    out, seq = [], Counter()

    def drop(why):
        drops[why] += 1
        log.info("drop %s p%s reason=%s", source, pages, why)

    for it in items:
        if not isinstance(it, dict):
            drop("invalid_schema")
            continue
        pg = it.get("page") if it.get("page") in pages else pages[0]
        sec = it.get("section") if it.get("section") in TAXONOMY else ("math" if math else "rw")
        ds = map_skill(sec, it.get("skill"), it.get("stem_pattern"), llm)
        if not ds:
            drop("skill_unmatched")
            continue
        seq[pg] += 1
        it = {**it, "section": sec, "domain": ds[0], "skill": ds[1], "id": f"{source}-p{pg}-{seq[pg]}",
              "source_tag": source, "page": pg}
        if it.get("visual_kind") not in VISUAL_KINDS:
            it["visual_kind"] = None
        try:
            bp = Blueprint.model_validate(it)
            if copies_source(blueprint_texts(bp), text):
                try:  # one rewrite retry: ask for a more abstract re-description
                    bp = Blueprint.model_validate(_reword(it, llm))
                except Exception:  # noqa: BLE001
                    drop("invalid_schema")
                    continue
                if copies_source(blueprint_texts(bp), text):
                    drop("guard_verbatim")
                    continue
            out.append(bp)
        except Exception:  # noqa: BLE001
            drop("invalid_schema")
    return out, drops


def chunk_pages(pages, size=2):
    """Group page numbers into runs of <=size adjacent pages."""
    out = []
    for p in sorted(pages):
        if out and len(out[-1]) < size and out[-1][-1] == p - 1:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def _meta(source):
    mf = Path(__file__).resolve().parents[1] / "reference" / "manifest.json"
    return json.loads(mf.read_text(encoding="utf-8")).get(source, {}) if mf.exists() else {}


def _chunk(source, pages_text, meta, llm):
    """One chunk -> (blueprints, drops). Blank/figure-only pages with no usable text are skipped (low_text)."""
    drops, use, partial = Counter(), {}, False
    for p, t in pages_text.items():
        n = len(t.strip())
        low = meta.get(str(p), {}).get("low_text", n < 100) or n < 100
        if low and (n < 40 or not (looks_math(t) or meta.get(str(p), {}).get("has_images"))):
            drops["low_text"] += 1
            log.info("drop %s p%d reason=low_text", source, p)
            continue
        partial |= low
        use[p] = t
    if not use:
        return [], drops
    text = "\n".join(f"=== PAGE {p} ===\n{t}" for p, t in use.items())
    bps, d = page_blueprints(source, min(use), text, llm, pages=list(use), partial=partial)
    return bps, drops + d


def extract_blueprints(source, pages=None, llm=default_llm, workers=4, state=None, out=None):
    """pages: iterable of page numbers (None = all). Chunks of 2 pages, parallel, resumable via the state file.
    Returns the Blueprints produced in this run (each chunk is merged into `out` as it finishes)."""
    want = set(pages) if pages else None
    allp = {p: t for p, t in iter_pages(source) if want is None or p in want}
    meta, state_path = _meta(source), Path(state or STATE)
    st = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    done = st.setdefault(source, {})
    todo = [c for c in chunk_pages(list(allp)) if f"{c[0]}-{c[-1]}" not in done]
    res = []

    def work(c):
        return c, _chunk(source, {p: allp[p] for p in c}, meta, llm)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, (bps, drops) in ex.map(work, todo):
            key = f"{c[0]}-{c[-1]}"
            log.info("%s p%s kept=%d drops=%s", source, key, len(bps), dict(drops))
            res.extend(bps)
            with _lock:
                merge_save(bps, out or OUT)
                if "llm_error" not in drops:  # llm failures stay pending and are retried on re-run
                    done[key] = {"kept": len(bps), "drops": dict(drops)}
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return res


def summary(source, state=None):
    """Print per-chunk lines and the per-reason drop table from the state file."""
    st = json.loads(Path(state or STATE).read_text(encoding="utf-8")).get(source, {})
    tot, kept = Counter(), 0
    for k, v in sorted(st.items(), key=lambda kv: int(kv[0].split("-")[0])):
        tot.update(v["drops"])
        kept += v["kept"]
        print(f"{source} p{k}: kept={v['kept']} drops={v['drops']}")
    print(f"{source}: chunks={len(st)} kept={kept}")
    for r in REASONS:
        print(f"  {r:16s}{tot[r]}")
    return tot


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
    return {k: dict(Counter(getattr(b, k) for b in bps)) for k in ("section", "domain", "skill", "difficulty")}


def parse_pages(s):
    if not s:
        return None
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for n in ("httpx", "google_genai", "sat_engine.llm", "urllib3"):
        logging.getLogger(n).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=["march2021", "sample4"])
    ap.add_argument("--pages")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    new = extract_blueprints(a.source, parse_pages(a.pages), workers=a.workers, out=a.out)
    summary(a.source)
    print(f"new={len(new)} total={len(load_blueprints(a.out))}")


if __name__ == "__main__":
    main()
