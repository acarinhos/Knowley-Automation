"""Independent solver / judge checks. verify() returns (ok, reason)."""
from __future__ import annotations

import json
from fractions import Fraction

from .. import llm
from ..visuals import describe, facts

SYSTEM = "You are an expert SAT solver. Solve from scratch. Reply with JSON only."


def to_number(s) -> float | None:
    try:
        t = str(s).strip().replace(",", "").replace("$", "").rstrip("%")
        return float(Fraction(t))
    except (ValueError, ZeroDivisionError):
        return None


def same_number(a, b, tol=1e-3) -> bool:
    x, y = to_number(a), to_number(b)
    return x is not None and y is not None and abs(x - y) <= tol * max(1, abs(y))


def _body(q) -> str:
    s = (f"Passage: {q.passage}\n" if q.passage else "") + f"Question: {q.stem}\n"
    if q.options:
        s += "".join(f"{k}. {v}\n" for k, v in q.options.items())
    if q.visual:
        s += f"Figure: {describe(q.visual['spec'])}\nFigure facts: {json.dumps(facts(q.visual['spec']))}\n"
    return s


def verify(q, providers=None) -> tuple[bool, str]:
    try:  # schema re-validation
        type(q).model_validate(q.model_dump())
    except ValueError as e:
        return False, f"schema: {str(e)[:100]}"
    if q.section == "math":
        r = llm.generate_json(_body(q) + 'Solve. JSON: {"answer": "final value", "choice": "letter or null"}',
                              system=SYSTEM, providers=providers)
        if q.answer_type == "mcq":
            choice = str(r.get("choice") or "").strip().upper()
            ok = choice == q.correct_answer if choice else same_number(r.get("answer"), q.options[q.correct_answer])
        else:
            ok = same_number(r.get("answer"), q.correct_answer)
        return (True, "") if ok else (False, "solver disagrees with key")
    r = llm.generate_json(_body(q) + 'Pick the best answer. JSON: {"choice": "A-D"}', system=SYSTEM, providers=providers)
    if str(r.get("choice", "")).strip().upper() != q.correct_answer:
        return False, "solver disagrees with key"
    j = llm.generate_json(_body(q) + 'List every option that is defensibly correct. JSON: {"defensible": ["A"]}',
                          system=SYSTEM, providers=providers)
    if sorted(str(x).strip().upper() for x in j.get("defensible", [])) != [q.correct_answer]:
        return False, "not exactly one defensible answer"
    return True, ""
