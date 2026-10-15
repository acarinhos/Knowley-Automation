"""Generate one original SATQuestion from a slot's abstract blueprint."""
from __future__ import annotations

import json
import uuid

from .. import llm
from ..schema import SATQuestion
from ..visuals import facts, parse_spec, render_svg

SYSTEM = ("You write ORIGINAL digital-SAT practice questions. Use a fresh topic and fresh numbers. "
          "Never reproduce or paraphrase any real exam text. Exactly one option must be correct. Reply with JSON only.")


def _numbers(o):
    if isinstance(o, bool):
        return
    if isinstance(o, (int, float)):
        yield float(o)
    elif isinstance(o, dict):
        for v in o.values():
            yield from _numbers(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _numbers(v)


def check_facts(claimed: dict, spec: dict) -> None:
    """Every figure value the question relies on must exist among the computed facts."""
    known = list(_numbers(facts(spec)))
    if not isinstance(claimed, dict) or not claimed:
        raise ValueError("visual question must list figure_values it relies on")
    for k, v in claimed.items():
        if not any(abs(float(v) - n) <= 1e-3 * max(1, abs(n)) for n in known):
            raise ValueError(f"figure value {k}={v} not supported by figure facts")


def build_prompt(slot) -> str:
    bp = slot.blueprint
    mcq = bp.get("answer_type", "mcq") != "grid_in"
    shape = {
        "passage": "string or null" if slot.section == "rw" else "null",
        "stem": "string",
        "options": "{A,B,C,D} strings" if mcq else "null (student-produced response)",
        "correct_answer": "letter A-D" if mcq else "number or fraction like 7/2",
        "explanation": "string"}
    if slot.needs_visual:
        shape["figure"] = ("figure spec JSON (kind: function_graph|scatter_with_line|bar_chart|histogram|dot_plot|"
                           "table|number_line|coordinate_lines|circle_sector|geometry)")
        shape["figure_values"] = "object of numbers from the figure that the question relies on"
    return (f"Section: {slot.section}; domain: {slot.domain}; skill: {slot.skill}; difficulty: {slot.difficulty}; module {slot.module}.\n"
            f"Abstract pattern (do not copy any source wording): stem pattern={bp.get('stem_pattern')}; "
            f"passage genre={bp.get('passage_genre')}; reasoning steps={bp.get('reasoning_steps')}; "
            f"distractor types={bp.get('distractor_types')}; answer type={'mcq' if mcq else 'grid_in'}.\n"
            f"Write a brand-new question with a new topic and new numbers. JSON shape: {json.dumps(shape)}")


def generate(slot, providers=None, qid: str | None = None, feedback: str | None = None) -> SATQuestion:
    prompt = build_prompt(slot) + (f"\nPrevious attempt rejected: {feedback}. Fix this." if feedback else "")
    d = llm.generate_json(prompt, system=SYSTEM, providers=providers)
    visual = None
    if slot.needs_visual:
        spec = d.get("figure")
        parse_spec(spec)  # raises on invalid
        check_facts(d.get("figure_values"), spec)
        visual = {"spec": spec, "svg": render_svg(spec)}
    ans = str(d.get("correct_answer", "")).strip()
    return SATQuestion(
        id=qid or uuid.uuid4().hex, section=slot.section, module=slot.module, difficulty=slot.difficulty,
        domain=slot.domain, skill=slot.skill, passage=d.get("passage") or None, stem=d.get("stem", ""),
        answer_type="grid_in" if slot.blueprint.get("answer_type") == "grid_in" else "mcq",
        options=d.get("options") or None, correct_answer=ans.upper() if len(ans) == 1 else ans,
        explanation=d.get("explanation", ""), visual=visual,
        meta={"blueprint_id": slot.blueprint.get("id")})
