"""Independent solver / judge checks. verify() returns (ok, reason)."""
from __future__ import annotations

import json
from fractions import Fraction

from .. import llm
from ..visuals import describe, facts

SYSTEM = "You are an expert SAT solver. Solve from scratch. Reply with JSON only."


CONVENTIONS = ("Boundaries", "Form, Structure, and Sense")


def judge_providers(q, providers=None):
    base = list(providers or llm.DEFAULT_ORDER)
    gen = q.meta.provider
    others = [p for p in base if p != gen]
    return others + [p for p in base if p == gen] if others else base


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
    # one call: the per-option judge covers every option, so its single acceptable option must equal the key
    providers = judge_providers(q, providers)  # judge != generator when possible
    conv = q.skill in CONVENTIONS
    j = llm.generate_json(
        _body(q) + "Evaluate EACH option independently: is it an acceptable, correct completion/answer on its own? "
        + ("For every option that is NOT acceptable, name the single rule it violates (subject-verb agreement, tense "
           "consistency, parallelism, punctuation, pronoun case, modifier placement, ...). " if conv else "")
        + 'JSON: {"options": {"A": {"acceptable": true, "reason": "...", "rule": "rule violated or null"}, "B": {...}, "C": {...}, "D": {...}}}',
        system=SYSTEM, providers=providers)
    ev = j.get("options") if isinstance(j.get("options"), dict) else {}
    ev = {str(k).strip().upper(): v for k, v in ev.items() if isinstance(v, dict)}
    if sorted(ev) != ["A", "B", "C", "D"]:
        return False, "judge did not evaluate every option"
    ok_opts = [k for k, v in ev.items() if str(v.get("acceptable")).strip().lower() in ("true", "yes")]
    if ok_opts != [q.correct_answer]:
        return False, "solver disagrees with key" if len(ok_opts) == 1 else "not exactly one acceptable option"
    if conv:
        rules = {k: str(ev[k].get("rule") or "").strip() for k in ev if k != q.correct_answer}
        if any(len(r_) < 3 or r_.lower() in ("none", "null", "n/a") for r_ in rules.values()):
            return False, "distractor without a named violated rule"
        q.explanation += " Distractor rules: " + "; ".join(f"{k}: {v}" for k, v in sorted(rules.items())) + "."
    return True, ""
