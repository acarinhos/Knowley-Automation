"""Mechanical formatting clean-up of an accepted session (no LLM). Safe fixes are applied; ambiguous cases are only reported.

Categories (fixed): literal_newline, latex, markdown, whitespace, math_minus, option_whitespace, option_period,
transition_comma, transition_case, blank_spacing, explanation_quote.   Ambiguous: latex_leftover, option_punct, option_case, blank,
transition_comma (2-2 tie), explanation_quote, schema.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from ..schema import SATQuestion
from . import verify
from .session import OUTPUT_DIR, audit_session, file_lock, read_store, write_store

CONVENTIONS = ("Boundaries", "Form, Structure, and Sense")  # options differ in punctuation/form on purpose
BLANK = "______"
_RE_NL = re.compile(r"\\n(?!eq\b|e(?![a-z])|u(?![a-z])|abla|ot\b|eg\b|leq|geq|mid|parallel|i(?![a-z]))")
_RE_ATOM = re.compile(r"-?[\w.]+")
_SYM = {r"\le": "≤", r"\leq": "≤", r"\ge": "≥", r"\geq": "≥", r"\ne": "≠", r"\neq": "≠", r"\cdot": "·", r"\times": "×",
        r"\pm": "±", r"\pi": "π", r"\theta": "θ", r"\equiv": "≡", r"\approx": "≈", r"\circ": "°"}
_RE_SYM = re.compile("|".join(re.escape(k) + r"(?![A-Za-z])" for k in sorted(_SYM, key=len, reverse=True)))
_RE_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_RE_DOLLAR = re.compile(r"(?<![\w\\])\$(\S(?:[^$\n]*\S)?)\$")  # $math$ (not "$5 ... $7": the closing $ must follow a non-space)
_RE_MATHY = re.compile(r"[=^_\{}+*/()<>]")
_RE_SIMPLE = re.compile(r"-?\d+(?:\.\d+)?|[A-Za-z](?:,[A-Za-z])*")
_RE_MOD = re.compile(r"\\pmod\s*\{?\s*(\w+)\s*\}?")


def _paren(s: str) -> str:
    s = s.strip()
    return s if _RE_ATOM.fullmatch(s) else f"({s})"


def clean_text(t: str, math: bool) -> tuple[str, Counter]:
    """Returns (cleaned, Counter of categories fixed)."""
    c: Counter = Counter()
    if not isinstance(t, str):
        return t, c

    def sub(cat, pat, rep, s, flags=0):
        n = re.subn(pat, rep, s, flags=flags) if isinstance(pat, str) else pat.subn(rep, s)
        if n[0] != s:
            c[cat] += 1
        return n[0]
    t = sub("literal_newline", _RE_NL, "\n", t)
    t = sub("latex", r"\\[()\[\]]|\$\$", "", t)
    t = sub("latex", _RE_DOLLAR, lambda m: m[1] if _RE_MATHY.search(m[1]) or _RE_SIMPLE.fullmatch(m[1]) else m.group(), t)
    t = sub("latex", _RE_FRAC, lambda m: f"{_paren(m[1])}/{_paren(m[2])}", t)
    t = sub("latex", _RE_MOD, r" (mod \1)", t)
    t = sub("latex", _RE_SYM, lambda m: _SYM[m.group()], t)
    t = sub("markdown", r"\*\*([^*\n]+)\*\*", r"\1", t)
    t = sub("markdown", r"`([^`\n]*)`", r"\1", t)
    t = sub("whitespace", r"(?<=\S)[ \t]{2,}(?=\S)", " ", t)
    t = sub("whitespace", r"[ \t]+(?=\n)|[ \t]+$", "", t, flags=re.M)
    t = sub("whitespace", r"\A\s+|\s+\Z", "", t)
    t = sub("blank_spacing", r"(?<=[A-Za-z0-9])(?=_{6}(?!_))|(?:(?<=[^_]_{6})|(?<=\A_{6}))(?=[A-Za-z0-9])", " ", t)  # 'ways______ increased' -> 'ways ______ increased'
    if math:
        t = sub("math_minus", r"\u2212| [\u2013\u2014] ", lambda m: "-" if m.group() == "\u2212" else " - ", t)
    return t, c


def _leftover_tex(q: SATQuestion) -> list[str]:
    s = " ".join(filter(None, [q.passage, q.stem, q.explanation, *(q.options or {}).values()]))
    return sorted(set(re.findall(r"\\[A-Za-z]+", s)))


# ---------------------------------------------------------------- option-set rules
def _blank_ctx(passage: str | None):
    """(sentence_start, followed_by_comma) of the first blank, or None."""
    m = re.search(r"_{2,}", passage or "")
    if not m:
        return None
    before = passage[:m.start()].rstrip()
    return (not before or before[-1] in ".!?:\n\"'"), passage[m.end():].startswith(",")


def _majority(flags: list[bool]):
    n = sum(flags)
    return True if n > len(flags) - n else False if n < len(flags) - n else None


def _fix_transitions(opts: dict, passage: str | None, fixes: list, amb: list) -> dict:
    ctx = _blank_ctx(passage)
    if ctx is None:
        return opts
    start, comma_after = ctx
    o = dict(opts)
    has = [v.endswith(",") for v in o.values()]
    want = False if comma_after else (True if start else _majority(has))
    if want is None:
        amb.append(("transition_comma", "options split 2-2 on trailing comma, blank mid-sentence"))
    else:
        for a, v in o.items():
            nv = v.rstrip(",") + ("," if want else "")
            if nv != v:
                o[a] = nv
                fixes.append(("transition_comma", f"{a}: trailing comma {'added' if want else 'removed'}"))
    for a, v in list(o.items()):  # case follows the blank position
        nv = (v[:1].upper() if start else v[:1].lower()) + v[1:]
        if nv != v:
            o[a] = nv
            fixes.append(("transition_case", f"{a}: first letter {'upper' if start else 'lower'}case"))
    return o


def _terminal(v: str) -> str:
    return v[-1] if v and v[-1] in ".,;:?!" else ""


def _fix_option_set(q: SATQuestion, opts: dict, fixes: list, amb: list) -> dict:
    if q.answer_type != "mcq" or q.skill in CONVENTIONS:
        return opts
    o = {a: v.strip() for a, v in opts.items()}
    for a in o:
        if o[a] != opts[a]:
            fixes.append(("option_whitespace", f"{a}: stray leading/trailing space"))
    if all(verify.to_number(v) is not None for v in o.values()):
        return o
    if q.skill == "Transitions" and q.section == "rw":
        return _fix_transitions(o, q.passage, fixes, amb)
    per = [v.endswith(".") for v in o.values()]
    if 0 < sum(per) < 4:  # mixed period: 3-1 normalises to the majority, 2-2 is ambiguous
        m = _majority(per)
        if m is None:
            amb.append(("option_period", "options split 2-2 on a final period"))
        else:
            for a, v in list(o.items()):
                nv = v.rstrip(".") + ("." if m else "") if v.endswith(".") != m else v
                if nv != v:
                    o[a] = nv
                    fixes.append(("option_period", f"{a}: final period {'added' if m else 'removed'}"))
    if len({_terminal(v) for v in o.values()}) > 1 and not any(k == "option_period" for k, _ in amb):
        amb.append(("option_punct", "options end with different punctuation: " + "".join(_terminal(v) or "_" for v in o.values())))
    if q.section == "rw":
        caps = {v[:1].isupper() for v in o.values() if v[:1].isalpha()}
        if len(caps) > 1:
            amb.append(("option_case", "options mix capitalised and lowercase first letters"))
    return o


# ---------------------------------------------------------------- explanation quotes
_RE_QUOTE = re.compile(r"(?<![\w])'([^'\n]{2,120})'(?![\w])|\"([^\"\n]{2,120})\"")


def _key(s: str) -> str:
    return re.sub(r"[\s.,;:!?'\"]+", " ", s).strip().casefold()


def _sync_quotes(q: SATQuestion, expl: str, opts: dict, fixes: list, amb: list) -> str:
    """A quoted span that equals an option except for punctuation/spacing becomes the exact option text;
    a near match (similar but different words) is reported."""
    if q.answer_type != "mcq" or q.skill in CONVENTIONS:
        return expl
    keyed = {_key(v): v for v in opts.values() if _key(v)}

    def rep(m):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        q_ = m.group()[0]
        k = _key(s)
        if k in keyed:
            exact = keyed[k]
            if s.strip() != exact and s.strip().casefold() != exact.casefold():
                fixes.append(("explanation_quote", f"quote {s[:30]!r} -> option text"))
                if s[:1].isalpha() and exact[:1].isalpha() and s[:1].isupper() != exact[:1].isupper():  # keep the quote's own case
                    exact = (s[:1].upper() if s[:1].isupper() else s[:1].lower()) + exact[1:]
                return f"{q_}{exact}{q_}"
            return m.group()
        if len(k) > 3 and any(difflib.SequenceMatcher(None, k, ok).ratio() >= 0.85 for ok in keyed):
            amb.append(("explanation_quote", f"quote {s[:40]!r} resembles an option but differs"))
        return m.group()
    return _RE_QUOTE.sub(rep, expl)


def _blank_check(q: SATQuestion, amb: list) -> None:
    if q.section != "rw":
        return
    blanks = re.findall(r"_+", q.passage or "")
    if any(b != BLANK for b in blanks):
        amb.append(("blank", f"blank marker(s) other than {BLANK}: {sorted(set(blanks))}"))
    if re.search(r"completes the text", q.stem, re.I) and not blanks and q.skill not in ("Inferences", "Boundaries", "Form, Structure, and Sense") \
            and q.skill in ("Words in Context", "Transitions"):
        amb.append(("blank", "stem asks to complete the text but the passage has no blank"))


def clean_question(q: SATQuestion) -> tuple[SATQuestion, list, list]:
    """(cleaned copy, fixes [(category, detail)], ambiguous [(category, detail)])."""
    fixes: list = []
    amb: list = []
    math = q.section == "math"
    c = q.model_copy(deep=True)

    def ct(t):
        out, cnt = clean_text(t, math)
        for k, n in cnt.items():
            fixes.extend([(k, "")] * n)
        return out
    c.passage, c.stem, c.explanation = (ct(c.passage) if c.passage else c.passage), ct(c.stem), ct(c.explanation)
    if c.options and not (c.skill in CONVENTIONS):  # convention options are punctuation/space puzzles: leave them alone
        c.options = {a: ct(v) for a, v in c.options.items()}
    if c.options:
        c.options = _fix_option_set(c, c.options, fixes, amb)
        c.explanation = _sync_quotes(c, c.explanation, c.options, fixes, amb)
    _blank_check(c, amb)
    for t in _leftover_tex(c):
        amb.append(("latex_leftover", t))
    try:
        c = SATQuestion.model_validate(c.model_dump())
    except ValueError as e:
        return q, [], amb + [("schema", f"cleaned item invalid, kept original: {str(e)[:80]}")]
    return c, fixes, amb


def next_backup(out: Path, tag: str = "pre_clean") -> Path:
    n = 1  # never overwrite an earlier backup
    while Path(f"{out}.{tag}{n if n > 1 else ''}.bak").exists():
        n += 1
    return Path(f"{out}.{tag}{n if n > 1 else ''}.bak")


def clean_session(sid: str, output_dir: Path = OUTPUT_DIR, dry_run: bool = False) -> dict:
    """Clean all accepted questions under the file lock; backup <file>.pre_clean[N].bak first. Returns counts and the ambiguous list."""
    out = Path(output_dir) / f"{sid}.json"
    rep = {"session_id": sid, "dry_run": dry_run, "fixed_by_category": {}, "items_changed": 0, "ambiguous": [], "ambiguous_items": 0}
    cats: Counter = Counter()

    def run():
        store = read_store(out)
        new, changed = dict(store), 0
        for i in sorted(store, key=int):
            q = SATQuestion.model_validate(store[i])
            c, fx, am = clean_question(q)
            cats.update({k for k, _ in fx})
            if c.model_dump() != q.model_dump():
                new[i] = c.model_dump()
                changed += 1
            rep["ambiguous"] += [{"slot": i, "category": k, "detail": d} for k, d in am]
        rep["items_changed"] = changed
        rep["ambiguous_items"] = len({a["slot"] for a in rep["ambiguous"]})
        if changed and not dry_run:
            rep["backup"] = str(shutil.copy2(out, next_backup(out)))
            write_store(out, new)

    if dry_run:
        run()
    else:
        with file_lock(out):
            run()
    rep["fixed_by_category"] = dict(cats)
    if not dry_run:
        rep["audit"] = audit_session(sid, output_dir)
    (Path(output_dir) / f"{sid}.clean_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return rep


def format_report(r: dict) -> str:
    lines = [f"clean {r['session_id']}{' (dry-run)' if r['dry_run'] else ''}: {r['items_changed']} items changed, fixed {r['fixed_by_category']}, "
             f"ambiguous {len(r['ambiguous'])} in {r['ambiguous_items']} items" + (f"; backup {r['backup']}" if r.get("backup") else "")]
    lines += [f"  ambiguous slot {a['slot']}: {a['category']} - {a['detail']}" for a in r["ambiguous"]]
    if "audit" in r:
        lines.append(f"  audit {r['audit']['failed']}/{r['audit']['total']} failed {r['audit']['by_check']}")
    return "\n".join(lines)
