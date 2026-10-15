"""Generate one original SATQuestion from a slot's abstract blueprint."""
from __future__ import annotations

import json
import uuid

from pydantic import ValidationError

from .. import llm
from ..schema import SATQuestion
from ..visuals import facts, parse_spec, render_svg

SYSTEM = ("You are a veteran College Board Digital SAT item writer. You write ORIGINAL practice questions in the exact "
          "style of the official Digital SAT. Use fresh topics and fresh numbers; never reproduce or paraphrase real exam "
          "text. Exactly one option is correct and every other option is demonstrably wrong. Reply with a single JSON "
          "object only (no markdown, no prose).")

RW_STEMS = {
    "Words in Context": "Which choice completes the text with the most logical and precise word or phrase?",
    "Text Structure and Purpose": "Which choice best states the main purpose of the text? (or: best describes the overall structure of the text)",
    "Cross-Text Connections": ("put both texts in the passage as 'Text 1: ...' and 'Text 2: ...' separated by a blank line; stem: "
                               "'Based on the texts, how would the author of Text 2 most likely respond to the claim in Text 1?'"),
    "Central Ideas and Details": "Which choice best states the main idea of the text? (or a detail question: 'According to the text, what ...?')",
    "Command of Evidence": ("Which finding, if true, would most directly support (or weaken) the claim? Or, with data, 'Which choice most "
                            "effectively uses data from the table to complete the statement?' (put the table rows in the passage as plain text)"),
    "Inferences": "Which choice most logically completes the text?",
    "Boundaries": ("Which choice completes the text so that it conforms to the conventions of Standard English? "
                   "(options differ in punctuation: commas, semicolons, colons, dashes, periods)"),
    "Form, Structure, and Sense": ("Which choice completes the text so that it conforms to the conventions of Standard English? "
                                   "(options differ in verb form, agreement, pronoun case, modifier placement)"),
    "Rhetorical Synthesis": ("While researching a topic, a student has taken the following notes: (3-5 bullet points in the passage). "
                             "Stem: 'The student wants to ... Which choice most effectively uses relevant information from the notes to accomplish this goal?'"),
    "Transitions": "Which choice completes the text with the most logical transition?",
}

RW_RULES = (
    "R&W rules: ONE short self-contained passage of about 25-150 words (literature, science, history or social science; "
    "vary topics; use advanced, precise vocabulary). NEVER refer to line numbers, 'underlined' text, or 'as used in line X'; "
    "for blanks use '______' inside the passage. Use the official stem wording for this skill. Four plausible, "
    "grammatically parallel, similar-length options; each wrong option must fail for a specific reason that follows the "
    "distractor types given (e.g. too extreme, out of scope, partly true, reverses the relationship, near-synonym with wrong "
    "connotation). Calibrate difficulty: easy = one clear clue; medium = needs careful reading; hard = subtle inference and "
    "close distractors that a careless reader finds tempting. The explanation must say why the key is right AND why each "
    "wrong option is wrong (one clause each).")
MATH_RULES = (
    "Math rules: realistic SAT contexts, clean numbers; passage must be null and the full problem is in the stem. Exactly "
    "one correct answer; compute it carefully and double-check. Wrong options must be results of typical student "
    "errors (sign slip, wrong operation, forgetting a step). Calibrate: easy = 1 step; medium = 2-3 steps; hard = multi-step "
    "or abstract reasoning. For a student-produced response the answer must be a single number (integer, decimal or simple "
    "fraction like 7/2), never a range, expression or unit. The explanation must show the solution steps and, for MCQ, "
    "name the error behind each wrong option. For a student-produced response there are NO answer choices: never mention 'choices', 'options' or 'wrong answers' in the explanation, and use null (not a string) for passage and options.")
EVIDENCE_RULE = (
    "Command of Evidence at this difficulty requires INFERENCE: the correct choice must paraphrase or logically entail the "
    "claim, never echo the passage's wording; the passage should supply evidence the student must interpret, and distractors "
    "should reuse passage words while failing to support (or while reversing) the claim.")

FIGURE_EXAMPLES = {
    "function_graph": {"kind": "function_graph", "curves": [{"type": "quadratic_vertex", "a": 1, "h": 2, "k": -4, "label": "f"}], "points": [{"x": 2, "y": -4, "label": "V"}], "x_label": "x", "y_label": "y"},
    "scatter_with_line": {"kind": "scatter_with_line", "points": [[1, 2], [2, 4.1], [3, 5.9], [4, 8.2]], "x_label": "Hours", "y_label": "Score"},
    "bar_chart": {"kind": "bar_chart", "categories": ["A", "B", "C"], "values": [3, 7, 5], "y_label": "Count"},
    "histogram": {"kind": "histogram", "bin_edges": [0, 10, 20, 30], "counts": [4, 9, 2]},
    "dot_plot": {"kind": "dot_plot", "values": [1, 2, 2, 3, 3, 3, 5]},
    "table": {"kind": "table", "columns": ["Year", "Sales"], "rows": [["2020", 15.5], ["2021", 20]]},
    "number_line": {"kind": "number_line", "min": -5, "max": 5, "points": [{"value": 1, "label": "a", "closed": False}], "intervals": [{"start": 2, "end": None, "start_closed": False}]},
    "coordinate_lines": {"kind": "coordinate_lines", "lines": [{"m": 1, "b": 1}, {"m": -1, "b": 5}]},
    "circle_sector": {"kind": "circle_sector", "radius": 6, "central_angle": 60},
    "triangle": {"kind": "geometry", "shape": "triangle", "sides": [5, 6, 7], "unknown": ["c"]},
    "right_triangle": {"kind": "geometry", "shape": "right_triangle", "a": 3, "b": 4, "hypotenuse": 5, "unknown": ["c"]},
    "circle": {"kind": "geometry", "shape": "circle", "radius": 5, "chord_length": 6},
    "rectangle": {"kind": "geometry", "shape": "rectangle", "width": 8, "height": 6, "diagonal": 10},
    "angles": {"kind": "geometry", "shape": "angles", "ray_angles": [0, 50, 180], "labels": [{"rays": [0, 1], "value": 50}, {"rays": [1, 2], "text": "x"}]},
    "parallel_lines": {"kind": "geometry", "shape": "parallel_lines", "transversal_angle": 65, "labels": [{"pos": 1, "value": 65}, {"pos": 6, "text": "x"}]},
}


def coerce_spec(spec):
    """LLMs often return the figure as a JSON string or wrapped as {"spec": {...}}."""
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            raise ValueError(f"figure is not valid JSON: {e}") from e
    if isinstance(spec, dict) and "kind" not in spec and isinstance(spec.get("spec"), (dict, str)):
        return coerce_spec(spec["spec"])
    if not isinstance(spec, dict):
        raise ValueError("figure must be a JSON object (a dict, not a string/list) with a 'kind' field")
    return spec


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
    rw = slot.section == "rw"
    shape = {
        "passage": "string (25-150 words, self-contained)" if rw else "null",
        "stem": "string",
        "options": "{A,B,C,D} strings" if mcq else "null (student-produced response)",
        "correct_answer": "letter A-D" if mcq else "a single number or fraction like 7/2",
        "explanation": "string"}
    extra = ""
    if slot.needs_visual:
        shape["figure"] = "a JSON OBJECT (not a string) figure spec"
        shape["figure_values"] = "object of numbers from the figure that the question relies on"
        kinds = [bp["visual_kind"]] if bp.get("visual_kind") in FIGURE_EXAMPLES else list(FIGURE_EXAMPLES)
        extra = ("\nThe stem must refer to the figure (e.g. 'The graph shown...'). Valid figure kinds with a compact example each; "
                 "pick the one that fits and copy its exact field names: "
                 + "; ".join(f"{k}: {json.dumps(FIGURE_EXAMPLES[k], separators=(',', ':'))}" for k in kinds))
    rules = RW_RULES + f"\nOfficial stem for this skill: {RW_STEMS.get(slot.skill, '')}" if rw else MATH_RULES
    if rw and slot.skill == "Command of Evidence" and slot.difficulty in ("medium", "hard"):
        rules += "\n" + EVIDENCE_RULE
    if rw and slot.skill in ("Boundaries", "Form, Structure, and Sense"):
        rules += ("\nConventions: exactly one option is grammatical; each of the three others must violate one named rule "
                  "(subject-verb agreement, tense consistency, parallelism, punctuation, pronoun case, modifier placement). "
                  "Check every distractor really is ungrammatical in context (e.g. do not offer two acceptable verb forms).")
    return (f"Section: {slot.section}; domain: {slot.domain}; skill: {slot.skill}; difficulty: {slot.difficulty}; module {slot.module}.\n"
            f"Abstract pattern (do not copy any source wording): stem pattern={bp.get('stem_pattern')}; "
            f"passage genre={bp.get('passage_genre')}; reasoning steps={bp.get('reasoning_steps')}; "
            f"distractor types={bp.get('distractor_types')}; answer type={'mcq' if mcq else 'grid_in'}.\n"
            f"{rules}{extra}\n"
            f"Write a brand-new question on a new topic with new numbers. JSON shape: {json.dumps(shape)}")


def generate(slot, providers=None, qid: str | None = None, feedback: str | None = None) -> SATQuestion:
    prompt = build_prompt(slot) + (f"\nPrevious attempt rejected: {feedback}. Fix this." if feedback else "")
    d = llm.generate_json(prompt, system=SYSTEM, providers=providers)
    visual = None
    if slot.needs_visual:
        spec = coerce_spec(d.get("figure"))
        try:
            parse_spec(spec)
        except ValidationError as e:  # compact message: the default one is a wall of union names
            raise ValueError("invalid figure spec: " + "; ".join(
                f"{'.'.join(map(str, x['loc'][1:]))}: {x['msg']}" for x in e.errors()[:4])) from e
        check_facts(d.get("figure_values"), spec)
        visual = {"spec": spec, "svg": render_svg(spec)}
    ans = str(d.get("correct_answer", "")).strip()
    return SATQuestion(
        id=qid or uuid.uuid4().hex, section=slot.section, module=slot.module, difficulty=slot.difficulty,
        domain=slot.domain, skill=slot.skill, passage=d.get("passage"), stem=d.get("stem", ""),
        answer_type="grid_in" if slot.blueprint.get("answer_type") == "grid_in" else "mcq",
        options=d["options"] if isinstance(d.get("options"), dict) and d["options"] else None, correct_answer=ans.upper() if len(ans) == 1 else ans,
        explanation=d.get("explanation", ""), visual=visual,
        meta={"blueprint_id": slot.blueprint.get("id"), **llm.last_used()})
