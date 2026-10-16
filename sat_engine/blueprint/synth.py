"""Synthetic abstract blueprints from the LLM's own knowledge of the digital SAT (no reference text involved)."""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import math
import re
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..llm import generate_json
from ..schema import TAXONOMY
from .extract import OUT, coverage, load_blueprints, merge_save
from .model import VISUAL_KINDS, Blueprint

PROVIDER_ORDER = ["groq", "cohere", "nvidia"]
DIFFS = ("easy", "medium", "hard")
SIM = 0.85
log = logging.getLogger("sat_engine.blueprint.synth")
_lock = threading.Lock()

SYSTEM = (
    "You are a digital-SAT test designer. From your own knowledge of the official Digital SAT specification, write "
    "ABSTRACT question blueprints (patterns), never concrete questions: no passages, no names, no specific numbers. "
    "Return JSON {\"blueprints\": [...]}.")
HARD_NOTE = ("HARD means genuinely hard: multi-step reasoning, subtle inference, close distractors that are each "
             "partly right, non-obvious setups that students must restructure. Reasoning steps must show the twist.")

_NL = "Nonlinear equations in one variable and systems of equations in two variables"
_ONE = "One-variable data: distributions and measures of center and spread"
# (section, skill, count, allowed visual kinds, hint)
VISUAL_NEEDS = (
    [("math", s, 2, ["geometry", "circle_sector", "coordinate_lines"], "figure-based variant") for s in
     ("Area and volume", "Lines, angles, and triangles", "Right triangles and trigonometry", "Circles")]
    + [("math", s, 2, ["function_graph", "coordinate_lines"], "graph/figure-based variant") for s in
       ("Equivalent expressions", _NL, "Nonlinear functions")]
    + [("math", s, 1, [k], f"data displayed as a {k}") for s, k in (
        ("Two-variable data: models and scatterplots", "scatter_with_line"), (_ONE, "histogram"),
        (_ONE, "dot_plot"), ("Ratios, rates, proportional relationships, and units", "table"),
        ("Probability and conditional probability", "table"), ("Percentages", "bar_chart"),
        ("Inference from sample statistics and margin of error", "bar_chart"))])
GRID_SKILLS = ["Linear equations in one variable", "Linear functions", "Percentages", "Area and volume",
               "Ratios, rates, proportional relationships, and units", "Right triangles and trigonometry",
               "Circles", "Equivalent expressions", _NL, "Probability and conditional probability", _ONE]
GRID_SHARE = 0.25


@dataclass
class Task:
    section: str
    domain: str
    skill: str
    difficulty: str
    n: int
    hint: str = ""
    visual_kinds: list = field(default_factory=list)
    grid_in: bool = False

    @property
    def key(self):
        return f"{self.section}/{self.skill}/{self.difficulty}{'/' + self.hint if self.hint else ''}"


def _domain(section, skill):
    return next(d for d, ss in TAXONOMY[section].items() if skill in ss)


def targets(n):
    return {"easy": n, "medium": n, "hard": n + 1}


def gaps(bps, min_per_cell=2) -> list[Task]:
    """Tasks still needed: cell shortfalls, then visual shortfalls, then grid_in share (recomputed every run)."""
    tg, have, tasks = targets(min_per_cell), Counter((b.section, b.skill, b.difficulty) for b in bps), []
    for sec, doms in TAXONOMY.items():
        for dom, skills in doms.items():
            for sk in skills:
                for d in DIFFS:
                    if (g := tg[d] - have[(sec, sk, d)]) > 0:
                        tasks.append(Task(sec, dom, sk, d, g))
    for sec, sk, need, kinds, hint in VISUAL_NEEDS:
        c = sum(b.section == sec and b.skill == sk and b.needs_visual and b.visual_kind in kinds for b in bps)
        if c < need:
            tasks.append(Task(sec, _domain(sec, sk), sk, "medium", need - c, hint, kinds))
    ms = [b for b in bps if b.section == "math"]
    # pending cell tasks will add math blueprints too, so size the grid_in quota on the post-fill total
    pending = sum(t.n for t in tasks if t.section == "math")
    miss = math.ceil(GRID_SHARE * (len(ms) + pending)) - sum(b.answer_type == "grid_in" for b in ms)
    for i in range(max(miss, 0)):
        sk = GRID_SKILLS[i % len(GRID_SKILLS)]
        tasks.append(Task("math", _domain("math", sk), sk, DIFFS[i % 2], 1, "student-produced numeric grid-in",
                          grid_in=True))
    return tasks


def _norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def is_dup(stem, others) -> bool:
    """`others` are already-normalised stems; near-duplicate = SequenceMatcher ratio >= SIM."""
    a = _norm(stem)
    return any(difflib.SequenceMatcher(None, a, o).ratio() >= SIM for o in others)


def _prompt(t: Task, k: int) -> str:
    lines = [f"Section {t.section}, domain {t.domain!r}, skill {t.skill!r}, difficulty {t.difficulty}. "
             f"Write {k} DISTINCT blueprints (different scenarios/structures from each other)."]
    if t.difficulty == "hard":
        lines.append(HARD_NOTE)
    if t.visual_kinds:
        lines.append(f"Each must depend on a figure: needs_visual true and visual_kind one of {t.visual_kinds} ({t.hint}).")
    elif t.section == "math":
        lines.append(f"If a figure is natural, needs_visual true with visual_kind one of {VISUAL_KINDS}; else false/null.")
    if t.grid_in:
        lines.append("answer_type must be grid_in (single numeric answer, no answer choices).")
    elif t.section == "math":
        lines.append("answer_type mcq unless the problem naturally has a single numeric answer (grid_in).")
    else:
        lines.append("answer_type mcq; include passage_genre (generic, e.g. 'science passage').")
    lines.append("Fields: stem_pattern (<=200 chars, abstract), reasoning_steps (3-6 short generic strings), "
                 "distractor_types (2-4 generic strings), answer_type, passage_genre (rw only), needs_visual, visual_kind.")
    return "\n".join(lines)


def make_blueprint(it, t: Task):
    """Validate one LLM item against its task; returns (Blueprint|None, reject_reason|None)."""
    if not isinstance(it, dict):
        return None, "not_object"
    it = {k: it.get(k) for k in ("stem_pattern", "reasoning_steps", "distractor_types", "answer_type",
                                 "passage_genre", "needs_visual", "visual_kind")}
    if it["visual_kind"] not in VISUAL_KINDS:
        it["visual_kind"] = None
    it["needs_visual"] = bool(it["needs_visual"]) and it["visual_kind"] is not None and t.section == "math"
    if not it["needs_visual"]:
        it["visual_kind"] = None
    it["answer_type"] = "grid_in" if t.grid_in else (it["answer_type"] if t.section == "math" else "mcq")
    if it["answer_type"] not in ("mcq", "grid_in"):
        it["answer_type"] = "mcq"
    if t.visual_kinds and it["visual_kind"] not in t.visual_kinds:
        return None, "wrong_visual"
    for k in ("reasoning_steps", "distractor_types"):
        if not isinstance(it[k], list) or not all(isinstance(x, str) for x in it[k]):
            return None, "invalid_schema"
    if len(it["reasoning_steps"]) < (3 if t.difficulty == "hard" else 2) or not it["distractor_types"]:
        return None, "too_thin"
    if not isinstance(it["stem_pattern"], str) or not it["stem_pattern"].strip():
        return None, "invalid_schema"
    if len(it["stem_pattern"]) > 200:
        return None, "stem_too_long"
    if re.search(r"\d{3,}", it["stem_pattern"]):
        return None, "concrete"
    try:
        return Blueprint.model_validate({**it, "id": f"synth-{uuid.uuid4().hex[:10]}", "section": t.section,
                                         "domain": t.domain, "skill": t.skill, "difficulty": t.difficulty,
                                         "source_tag": "synthetic", "page": 0}), None
    except Exception:  # noqa: BLE001
        return None, "invalid_schema"


def default_llm(prompt, system=None):
    return generate_json(prompt, system=system, providers=PROVIDER_ORDER)


def run_task(t: Task, existing: list[Blueprint], llm=default_llm):
    """One LLM call for a task -> (new blueprints, rejects Counter). `existing` = current blueprints (for dedupe)."""
    rej, out = Counter(), []
    try:
        data = llm(_prompt(t, min(t.n + 1, 5)), system=SYSTEM)
    except Exception as e:  # noqa: BLE001
        rej["llm_error"] += 1
        log.info("reject %s reason=llm_error %s", t.key, type(e).__name__)
        return out, rej
    items = data.get("blueprints", []) if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        rej["empty"] += 1
        return out, rej
    seen = [_norm(b.stem_pattern) for b in existing if b.section == t.section and b.skill == t.skill]
    for it in items:
        bp, why = make_blueprint(it, t)
        if bp and is_dup(bp.stem_pattern, seen):
            bp, why = None, "duplicate"
        if bp is None:
            rej[why] += 1
            log.info("reject %s reason=%s", t.key, why)
            continue
        seen.append(_norm(bp.stem_pattern))
        out.append(bp)
    return out, rej


def synthesize(min_per_cell=2, llm=default_llm, workers=4, out=OUT, rounds=4):
    """Fill gaps (recomputed from the file each round, so re-runs resume). Returns (n_new, total rejects)."""
    total, new = Counter(), 0
    for r in range(rounds):
        snap = load_blueprints(out)  # workers read only this snapshot; the file is touched under the lock
        tasks = gaps(snap, min_per_cell)
        log.info("round %d: %d tasks, need %d blueprints", r + 1, len(tasks), sum(t.n for t in tasks))
        if not tasks:
            break

        def work(t):
            return t, run_task(t, snap, llm)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for t, (bps, rej) in ex.map(work, tasks):
                total.update(rej)
                with _lock:  # re-dedupe against what earlier results saved meanwhile
                    cur = load_blueprints(out)
                    keep = []
                    for b in bps:
                        pool = [_norm(x.stem_pattern) for x in cur + keep if x.section == b.section and x.skill == b.skill]
                        if is_dup(b.stem_pattern, pool):
                            total["duplicate"] += 1
                        else:
                            keep.append(b)
                    if keep:
                        merge_save(keep, out)
                    new += len(keep)
                log.info("%s kept=%d rejects=%s", t.key, len(keep), dict(rej))
    return new, total


def unfilled(min_per_cell=2, out=OUT):
    return [(t.key, t.n) for t in gaps(load_blueprints(out), min_per_cell)]


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for n in ("httpx", "google_genai", "sat_engine.llm", "urllib3"):
        logging.getLogger(n).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-per-cell", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    new, rej = synthesize(a.min_per_cell, workers=a.workers, out=a.out, rounds=a.rounds)
    print(f"new={new} rejects={dict(rej)}")
    print(json.dumps(coverage(load_blueprints(a.out))))
    print("unfilled:", unfilled(a.min_per_cell, a.out))


if __name__ == "__main__":
    main()
