"""Deterministic session plan: 54 R&W (27+27) and 44 Math (22+22) slots."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from ..config import MODULE_SIZE
from ..schema import TAXONOMY

BLUEPRINTS_PATH = Path(__file__).resolve().parents[1] / "blueprints" / "blueprints.json"
DOMAIN_WEIGHTS = {
    "rw": {"Craft and Structure": 28, "Information and Ideas": 26, "Standard English Conventions": 26, "Expression of Ideas": 20},
    "math": {"Algebra": 35, "Advanced Math": 35, "Problem-Solving and Data Analysis": 15, "Geometry and Trigonometry": 15},
}
DIFF_WEIGHTS = {1: (45, 40, 15), 2: (15, 40, 45)}  # easy, medium, hard
DIFFS = ("easy", "medium", "hard")
VISUAL_FRACTION = 0.25
GRID_FRACTION = 0.25  # share of math items (non-visual) that are student-produced responses
NO_GRID_SKILLS = {"Evaluating statistical claims: observational studies and experiments", "Equivalent expressions"}
VISUAL_SKILLS = {"Linear functions", "Linear equations in two variables", "Systems of two linear equations in two variables",
                 "Nonlinear functions", "Two-variable data: models and scatterplots",
                 "One-variable data: distributions and measures of center and spread"}


@dataclass
class Slot:
    index: int
    section: str
    module: int
    domain: str
    skill: str
    difficulty: str
    needs_visual: bool = False
    blueprint: dict = field(default_factory=dict)


def load_blueprints(path: Path = BLUEPRINTS_PATH) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [b for b in data if isinstance(b, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _apportion(total: int, weights: list[float]) -> list[int]:
    """Largest-remainder rounding so the counts sum exactly to total."""
    s = sum(weights)
    raw = [total * w / s for w in weights]
    out = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[: total - sum(out)]:
        out[i] += 1
    return out


def _synthetic(section: str, domain: str, skill: str, difficulty: str, visual: bool) -> dict:
    return {"id": f"synthetic-{section}-{skill}".lower().replace(" ", "-"), "section": section, "domain": domain,
            "skill": skill, "difficulty": difficulty, "answer_type": "mcq", "passage_genre": None,
            "stem_pattern": None, "reasoning_steps": [], "distractor_types": [], "needs_visual": visual,
            "visual_kind": None, "source_tag": "synthetic", "page": None}


def _pick_blueprint(pool, rng, section, domain, skill, diff, visual):
    c = [b for b in pool if b.get("section") == section and b.get("domain") == domain and b.get("skill") == skill]
    if not c:
        return _synthetic(section, domain, skill, diff, visual)
    # prefer matching difficulty and visual need; fall back to any blueprint of the skill
    for pred in (lambda b: b.get("difficulty") == diff and bool(b.get("needs_visual")) == visual,
                 lambda b: bool(b.get("needs_visual")) == visual, lambda b: True):
        m = [b for b in c if pred(b)]
        if m:
            return dict(rng.choice(m))


def build_plan(seed: int = 0, blueprints: list[dict] | None = None, sections=("rw", "math")) -> list[Slot]:
    rng = random.Random(seed)
    pool = load_blueprints() if blueprints is None else blueprints
    slots: list[Slot] = []
    for section in sections:
        sec_slots = []
        for module in (1, 2):
            n = MODULE_SIZE[section]
            doms = list(DOMAIN_WEIGHTS[section])
            diffs = [d for d, c in zip(DIFFS, _apportion(n, list(DIFF_WEIGHTS[module]))) for _ in range(c)]
            rng.shuffle(diffs)
            pairs = []
            for dom, c in zip(doms, _apportion(n, list(DOMAIN_WEIGHTS[section].values()))):
                skills = TAXONOMY[section][dom]
                pairs += [(dom, skills[(i + rng.randrange(len(skills))) % len(skills)]) for i in range(c)]
            rng.shuffle(pairs)
            sec_slots += [Slot(0, section, module, d, s, df) for (d, s), df in zip(pairs, diffs)]
        if section == "math":
            want = round(VISUAL_FRACTION * len(sec_slots))
            cand = [s for s in sec_slots if s.domain == "Geometry and Trigonometry" or s.skill in VISUAL_SKILLS]
            rng.shuffle(cand)
            for s in cand[:want]:
                s.needs_visual = True
        for s in sec_slots:
            s.blueprint = _pick_blueprint(pool, rng, s.section, s.domain, s.skill, s.difficulty, s.needs_visual)
        if section == "math":
            cand = [s for s in sec_slots if not s.needs_visual and s.skill not in NO_GRID_SKILLS]
            rng.shuffle(cand)
            for s in cand[: round(GRID_FRACTION * len(sec_slots))]:
                s.blueprint["answer_type"] = "grid_in"
        slots += sec_slots
    for i, s in enumerate(slots):
        s.index = i
    return slots
