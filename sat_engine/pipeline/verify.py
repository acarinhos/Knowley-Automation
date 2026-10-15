"""Independent solver / judge checks. verify() returns (ok, reason)."""
from __future__ import annotations

import json
import re
from fractions import Fraction

from .. import llm
from ..visuals import describe, facts

SYSTEM = "You are an expert SAT solver. Solve from scratch. Reply with JSON only."


CONVENTIONS = ("Boundaries", "Form, Structure, and Sense")


def judge_providers(q, providers=None):
    """Preference order only; the HARD generator exclusion is `exclusions(q)` passed to llm."""
    base = list(providers or llm.default_order())
    gen = q.meta.provider
    if gen == "external":  # imported item: nobody to exclude, but a non-claude judge is preferred
        return [p for p in base if p != "claude"] + [p for p in base if p == "claude"]
    others = [p for p in base if p != gen]
    return others + [p for p in base if p == gen] if others else base


def exclusions(q) -> list[str]:
    gen = q.meta.provider
    return [gen] if gen and gen != "external" else []


_FRAC = re.compile(r"\\[dt]?frac\s*\{\s*(-?\d+(?:\.\d+)?)\s*\}\s*\{\s*(\d+(?:\.\d+)?)\s*\}")
_TEX = re.compile(r"\\left|\\right|\\[()]|[$\s,]")


def to_number(s) -> Fraction | None:
    r"""Exact value of ints, decimals, a/b fractions, %, $ and simple LaTeX \frac{a}{b}; None if not a plain number."""
    try:
        t = _TEX.sub("", _FRAC.sub(lambda m: f"{m[1]}/{m[2]}", str(s).replace("\u2212", "-"))).rstrip("%")
        return Fraction(t)
    except (ValueError, ZeroDivisionError):
        return None


def same_number(a, b, tol=1e-6) -> bool:
    """Exact when the key b is an integer, otherwise relative tolerance `tol`."""
    x, y = to_number(a), to_number(b)
    if x is None or y is None:
        return False
    return x == y if y.denominator == 1 else abs(x - y) <= Fraction(tol) * abs(y)


def duplicate_numeric_options(options) -> bool:
    """True when two options of a math MCQ have the same numeric value."""
    vals = [to_number(v) for v in (options or {}).values()]
    vals = [v for v in vals if v is not None]
    return len(vals) != len(set(vals))


def key_dominates(options, key, ratio=1.25, min_gap=5) -> bool:
    """True when the key is the strictly longest option by > `ratio` (and >= min_gap chars) of a non-numeric mcq."""
    if not options or key not in options or all(to_number(v) is not None for v in options.values()):
        return False
    k = len(options[key].strip())
    m = max(len(v.strip()) for a, v in options.items() if a != key)
    return k > ratio * m and k - m >= min_gap


def _norm(s) -> str:
    return _TEX.sub("", str(s)).lower()


def math_ok(q, r: dict) -> bool:
    """Solver result r must agree with the key; a returned letter must also match the returned answer."""
    ans = str(r.get("answer") if r.get("answer") is not None else "").strip()
    if q.answer_type != "mcq":
        return same_number(ans, q.correct_answer)
    choice = str(r.get("choice") or "").strip().upper()
    if choice not in q.options:
        choice = ""
    if choice and choice != q.correct_answer:
        return False
    if ans:
        key = q.options[q.correct_answer]
        if not (same_number(ans, key) or (to_number(ans) is None and to_number(key) is None and _norm(ans) == _norm(key))):
            return False
    return bool(choice or ans)


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
    if q.answer_type == "mcq" and key_dominates(q.options, q.correct_answer):  # cheap soft check before any LLM call
        return False, "key is the longest option by >25%: make every distractor as long, specific and detailed as the key"
    if q.section == "math":
        if q.answer_type == "mcq" and duplicate_numeric_options(q.options):
            return False, "math options are numerically equal"
        r = llm.generate_json(_body(q) + 'Solve. JSON: {"answer": "final value", "choice": "letter or null"}',
                              system=SYSTEM, providers=judge_providers(q, providers), exclude=exclusions(q))
        return (True, "") if math_ok(q, r) else (False, "solver disagrees with key")
    # one call: the per-option judge covers every option, so its single acceptable option must equal the key
    providers = judge_providers(q, providers)  # soft order; exclusions() is the hard rule
    conv = q.skill in CONVENTIONS
    j = llm.generate_json(
        _body(q) + "Evaluate EACH option independently: is it an acceptable, correct completion/answer on its own? "
        + ("For every option that is NOT acceptable, name the single rule it violates (subject-verb agreement, tense "
           "consistency, parallelism, punctuation, pronoun case, modifier placement, ...). " if conv else "")
        + 'JSON: {"options": {"A": {"acceptable": true, "reason": "...", "rule": "rule violated or null"}, "B": {...}, "C": {...}, "D": {...}}}',
        system=SYSTEM, providers=providers, exclude=exclusions(q))
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
