"""Post-hoc quality balancing of an accepted session: (a) key-length bias, (b) key-letter position balance."""
from __future__ import annotations

import logging
import random
import re
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import llm
from ..schema import SATQuestion
from . import originality, verify
from .session import OUTPUT_DIR, audit_session, file_lock, read_store, write_store

log = logging.getLogger("sat_engine.balance")
LETTERS = "ABCD"
MIN_GAP = 5  # characters; below this a length difference is noise, not a tell
SYSTEM = "You are a veteran College Board Digital SAT item editor. Reply with a single JSON object only."


def _lens(options, key):
    return len(options[key].strip()), [len(v.strip()) for k, v in options.items() if k != key]


def key_is_longest(options, key, ratio=1.15, mean_ratio=1.25) -> bool:
    """Key strictly longest by > `ratio`, or longer than the mean distractor by > `mean_ratio` (both by >= MIN_GAP chars)."""
    k, others = _lens(options, key)
    mean = sum(others) / 3
    return (k > ratio * max(others) and k - max(others) >= MIN_GAP) or (k > mean_ratio * mean and k - mean >= MIN_GAP)


def numeric_options(options) -> bool:
    return all(verify.to_number(v) is not None for v in options.values())


def needs_length_fix(q) -> bool:
    return q.answer_type == "mcq" and not numeric_options(q.options) and key_is_longest(q.options, q.correct_answer)


# ---------------------------------------------------------------- (a) length bias
BAND = 0.20            # key length must be within +-20% of the mean distractor length ...
BAND_TARGET = 0.80     # ... for at least this share of (non-numeric) mcq per section
LONGEST_TARGET = 0.35  # key strictly longest in at most this share of mcq per section (chance level 25%)
STOP_MARGIN = 0.03     # the rewrite loop aims this far inside the targets so the verified result stays inside them
TRIES = 3              # rewrite attempts per item, each avoiding providers already used for that item


def ratio(options, key) -> float:
    k, o = _lens(options, key)
    return k / max(1.0, sum(o) / 3)


def strictly_longest(options, key) -> bool:
    k, o = _lens(options, key)
    return k > max(o)


def in_band(options, key) -> bool:
    return abs(ratio(options, key) - 1) <= BAND


def length_ok(options, key) -> bool:
    """Acceptance test for a rewritten item: inside the band and the key is not the single longest option."""
    return in_band(options, key) and not strictly_longest(options, key)


def length_stats(qs) -> dict:
    """Per section over ALL mcq: key strictly longest; within-band share over non-numeric mcq (numeric option length carries no cue)."""
    out = {}
    for q in qs:
        if q.answer_type != "mcq":
            continue
        s = out.setdefault(q.section, {"mcq": 0, "longest": 0, "nonnumeric": 0, "within": 0})
        s["mcq"] += 1
        s["longest"] += strictly_longest(q.options, q.correct_answer)
        if not numeric_options(q.options):
            s["nonnumeric"] += 1
            s["within"] += in_band(q.options, q.correct_answer)
    for s in out.values():
        s["longest_share"] = round(s["longest"] / s["mcq"], 3)
        s["within_share"] = round(s["within"] / max(1, s["nonnumeric"]), 3)
    return out


def length_candidates(qs: dict, margin: float = STOP_MARGIN, skip=()) -> list:
    """Items worth rewriting, worst first; only sections still outside their (margin-tightened) targets contribute."""
    st = length_stats(qs.values())
    need_long = {s for s, v in st.items() if v["longest_share"] > LONGEST_TARGET - margin}
    need_band = {s for s, v in st.items() if v["within_share"] < BAND_TARGET + margin}
    c = []
    for i, q in qs.items():
        if q.answer_type != "mcq" or i in skip or numeric_options(q.options):
            continue
        k = q.correct_answer
        if (q.section in need_long and strictly_longest(q.options, k)) or (q.section in need_band and not in_band(q.options, k)):
            c.append((-abs(ratio(q.options, k) - 1), i))
    return [i for _, i in sorted(c)]


def _strip_rules(expl: str) -> str:
    return re.sub(r"\s*Distractor rules:.*$", "", expl, flags=re.S)


def _prompt(q) -> str:
    k = len(q.options[q.correct_answer].strip())
    opts = "".join(f"{a}. {v}\n" for a, v in q.options.items())
    return (f"{('Passage: ' + q.passage + chr(10)) if q.passage else ''}Question: {q.stem}\nOptions:\n{opts}"
            f"Correct option: {q.correct_answer} ({k} characters)\nExplanation: {_strip_rules(q.explanation)}\n\n"
            "The length of the correct option does not match the wrong options, which is a test-taking giveaway. Rewrite ONLY the "
            "three wrong options (distractors): enrich or lengthen short distractors with equally specific, same-type detail "
            f"(never shorten or touch the correct option) so each is {int(k * .9)}-{int(k * 1.15) + 1} characters (the correct option has {k}), "
            "with the same grammatical form, specificity and level of detail. Make the longest distractor slightly longer than the "
            "correct option. Keep what makes each distractor wrong (same error type) and keep every "
            "distractor clearly wrong, never arguable or correct. Do not change the correct option, the stem or the passage; "
            "distractors must stay distinct from each other. Keep numeric/algebraic values of math distractors meaningful. "
            "Also rewrite the explanation so every statement about a wrong option matches its new text; keep the letter labels "
            'and the reasoning for the correct option. JSON: {"options": {<letter>: "new text" for each wrong option only}, "explanation": "..."}')


NO_PROV_MSG = "uzunluk düzeltmesi için claude dışında bir sağlayıcı seçin"


def rewrite_distractors(q, providers=None, exclude=()):
    """One LLM rewrite via the health-routed provider layer: ANY provider incl. the generator (a 429 falls to the next one),
    `claude` excluded; `exclude` = providers already used for this item. Returns a modified copy or raises ValueError."""
    prov = [p for p in (llm.default_order() if providers is None else providers) if p != "claude"]
    if not prov:
        raise ValueError(NO_PROV_MSG)
    d = llm.generate_json(_prompt(q), system=SYSTEM, providers=prov, exclude=[e for e in exclude if e])
    new = d.get("options")
    if not isinstance(new, dict):
        raise ValueError("no options in rewrite")
    new = {str(a).strip().upper(): str(v).strip() for a, v in new.items()}
    opts = dict(q.options)
    for a in LETTERS:
        if a != q.correct_answer:
            if not new.get(a):
                raise ValueError("missing distractor")
            opts[a] = new[a]
    if new.get(q.correct_answer, opts[q.correct_answer]).strip() != q.options[q.correct_answer].strip():
        raise ValueError("rewrite changed the key")
    expl = str(d.get("explanation") or "").strip()
    if len(expl) < 20:
        raise ValueError("no explanation in rewrite")
    if not length_ok(opts, q.correct_answer):
        raise ValueError("rewrite still biased")
    c = q.model_copy(deep=True)
    c.options, c.explanation = opts, expl
    return SATQuestion.model_validate(c.model_dump())


def fix_length(q, chk, providers=None, tries=TRIES):
    """Returns (new_q or None, reason). Rewrite -> per-option verify (judge != generator, hard) -> originality.
    Each retry avoids providers already used for this item."""
    why, used = "no attempt", []
    if providers is not None and not [p for p in providers if p != "claude"]:
        return None, NO_PROV_MSG
    for _ in range(tries):
        try:
            c = rewrite_distractors(q, providers, exclude=used)
            used.append((getattr(llm._last, "info", None) or {}).get("provider"))
            ok, why = verify.verify(c, providers)
            if ok and not chk.check(c)[0]:
                ok, why = False, "originality"
        except Exception as e:  # noqa: BLE001 - LLM/schema errors all mean "try again"
            ok, why, c = False, f"{type(e).__name__}: {str(e)[:80]}", None
        if ok:
            return c, ""
    return None, why


# ---------------------------------------------------------------- (b) position balance
_REF = re.compile(r"\b(?i:all of the above|none of the above|neither of the above)\b|\b(?i:both|either|neither)\s+[A-D]\b"
                  r"|\b[A-D]\s+(?:and|or)\s+[A-D]\b|\b(?i:option|choice)s?\s+[A-D]\b")
_PH, _PE = "", ""
_L = r"[A-D](?:\s*(?:,|and|or|&|/)\s*(?:and\s+|or\s+)?[A-D])*"
_START = r"(?:(?<![\s\S])|(?<=[.;!?:,] )|(?<=\n)|(?<=\band )|(?<=\bbut )|(?<=\bwhile )|(?<=\bwhereas )|(?<=\byet ))"
_VERBS = set("is are was were correctly incorrectly mistakenly erroneously wrongly improperly fails uses gives states suggests presents "
             "misstates reverses breaks shifts inserts introduces offers provides contains creates includes has does would also only merely "
             "mixes places omits adds claims describes confuses overstates understates ignores repeats lacks implies misreads treats".split())
_MAP_RE = re.compile(
    rf"(?P<kw>\b(?:[Oo]ptions?|[Cc]hoices?|[Aa]nswers?|[Dd]istractors?|[Ll]etters?|[Kk]ey)(?:\s+is)?\s+)(?P<l1>{_L})(?![\w'])"
    rf"|\((?P<l2>{_L})\)"
    rf"|(?<![\w(])(?P<l3>[A-D])(?=\s*[:)])"
    rf"|(?<=\bthan )(?P<l5>[A-D])(?![\w'])|(?<=\bwith )(?P<l6>[A-D])(?![\w'])|(?<=\bto )(?P<l7>[A-D])(?![\w'])"
    rf"|{_START}(?P<l4>{_L})(?=\s+(?P<nxt>[a-z]+))"
    r"|(?<![\w(])(?P<l8>[A-D](?:(?:,\s*|\s+)(?:and|or)\s+|,\s*)[A-D](?:(?:,\s*|\s+)(?:and|or)\s+|,\s*[A-D])*)(?![\w'])")
_RESID = re.compile(r"(?<![\w.'/-])[B-D](?![\w'/-])")


def _sub_letters(s, m):
    return re.sub("[A-D]", lambda x: f"{_PH}{LETTERS.index(m[x.group()])}{_PE}", s)


def remap_explanation(text: str, m: dict, context: str = ""):
    """Rewrite every option-letter reference through the old->new map `m` in ONE pass (placeholders, so A->B, B->A cannot
    chain). Returns None when a bare B/C/D remains that is not a recognised reference (item must not be shuffled)."""
    ctx = set(_RESID.findall(context))  # letters the stem/passage uses as names (points, vertices): bare references are ambiguous
    clash = []

    def rep(x):
        g = x.groupdict()
        if g["kw"]:
            return g["kw"] + _sub_letters(g["l1"], m)
        if g["l4"] == "A" and g["nxt"] not in _VERBS:
            return x.group()  # the article "A"
        if not g["l2"] and ctx & set(re.findall("[B-D]", x.group())):
            clash.append(1)
        return _sub_letters(x.group(), m)
    t = _MAP_RE.sub(rep, text)
    if clash:
        return None
    if any(r not in ctx for r in _RESID.findall(t)):
        return None
    t = re.sub(f"{_PH}([0-3]){_PE}", lambda x: LETTERS[int(x[1])], t)
    return re.sub(r"Distractor rules: (.*?)\.\s*$", lambda x: "Distractor rules: " + "; ".join(sorted(x[1].split("; "))) + ".", t, flags=re.S)


def shuffle_class(q) -> str | None:
    """'free', 'reverse' (only A<->D, B<->C keeps a sorted numeric order) or None (options refer to each other)."""
    if q.answer_type != "mcq" or any(_REF.search(v) for v in q.options.values()):
        return None
    vals = [verify.to_number(q.options[a]) for a in LETTERS]
    if q.section == "math" and None not in vals and (vals == sorted(vals) or vals == sorted(vals, reverse=True)):
        return "reverse"
    return "free"


_REV = {"A": "D", "B": "C", "C": "B", "D": "A"}


def _apply(q, target, m=None) -> bool:
    """Move the key to `target` (others keep their relative order) or via explicit map m. False (q untouched) when the
    explanation cannot be remapped safely."""
    k = q.correct_answer
    if m is None:
        m = {k: target, **dict(zip([a for a in LETTERS if a != k], [a for a in LETTERS if a != target]))}
    expl = remap_explanation(q.explanation, m, f"{q.stem} {q.passage or ''}")
    if expl is None:
        return False
    q.options = {m[a]: q.options[a] for a in LETTERS}
    q.options = {a: q.options[a] for a in LETTERS}
    q.correct_answer, q.explanation = m[k], expl
    return True


def balance_positions(qs: dict, movable=None, seed: int = 0) -> dict:
    """In-place: permute options of mcq items so key letters are as equal as possible per section and overall.
    `qs`: key -> SATQuestion; `movable`: keys allowed to change (default all). Returns counts."""
    rng = random.Random(seed)
    mcq = {i: q for i, q in qs.items() if q.answer_type == "mcq"}
    mov = set(mcq if movable is None else movable) & set(mcq)
    cls = {i: shuffle_class(mcq[i]) for i in mov}
    stats = {"mcq": len(mcq), "fixable": sum(1 for c in cls.values() if c), "fixed": 0,
             "unfixable_reference": sum(1 for c in cls.values() if not c), "unfixable_explanation": 0}
    overall = Counter(q.correct_answer for q in mcq.values())
    for sec in sorted({q.section for q in mcq.values()}):
        items = [i for i, q in mcq.items() if q.section == sec]
        other = overall - Counter(mcq[i].correct_answer for i in items)
        base, rem = divmod(len(items), 4)
        extra = sorted(LETTERS, key=lambda a: (other[a], a))[:rem]
        free = [i for i in items if cls.get(i)]
        cur = Counter(mcq[i].correct_answer for i in items if i not in free)
        deficit = {a: base + (a in extra) - cur[a] for a in LETTERS}
        rng.shuffle(free)
        target, rest = {}, []
        for i in (x for x in free if cls[x] == "reverse"):
            o = mcq[i].correct_answer
            target[i] = _REV[o] if deficit[_REV[o]] > deficit[o] else o
            deficit[target[i]] -= 1
        for i in (x for x in free if cls[x] == "free"):
            o = mcq[i].correct_answer
            if deficit[o] > 0:  # already at a letter that still needs keys: stay
                deficit[o] -= 1
                target[i] = o
            else:
                rest.append(i)
        for i in rest:
            target[i] = max(LETTERS, key=lambda a: (deficit[a], -LETTERS.index(a)))
            deficit[target[i]] -= 1
        for i, t in target.items():
            q = mcq[i]
            if t != q.correct_answer:
                if _apply(q, t, _REV if cls[i] == "reverse" else None):
                    stats["fixed"] += 1
                else:
                    stats["unfixable_explanation"] += 1
    return stats


def key_stats(qs) -> dict:
    overall, secs, longest = Counter(), {}, Counter()
    for q in qs:
        if q.answer_type == "mcq":
            overall[q.correct_answer] += 1
            secs.setdefault(q.section, Counter())[q.correct_answer] += 1
            k, o = _lens(q.options, q.correct_answer)
            longest[q.section] += k > max(o)
    return {"overall": dict(sorted(overall.items())), "sections": {s: dict(sorted(c.items())) for s, c in secs.items()},
            "key_strictly_longest": dict(longest)}


# ---------------------------------------------------------------- session driver
def balance_session(sid: str, output_dir: Path = OUTPUT_DIR, dry_run: bool = False, providers=None, workers: int = 4,
                    seed: int = 0, checker=None, progress=print) -> dict:
    """LLM work runs on a snapshot WITHOUT the file lock (it can take long and locks go stale); the result is merged under
    the lock, only into items still identical to the snapshot, after writing <file>.pre_balance.bak. dry_run: no LLM, no write."""
    out = Path(output_dir) / f"{sid}.json"
    snap = read_store(out)
    qs = {i: SATQuestion.model_validate(d) for i, d in snap.items()}
    chk = checker or originality.OriginalityChecker()
    rep = {"session_id": sid, "dry_run": dry_run, "total": len(qs), "length_biased": len(length_candidates(qs, margin=0)),
           "length_fixed": 0, "length_failed": {}, "before": key_stats(qs.values()), "length_before": length_stats(qs.values())}
    changed: set = set()
    tried: set = set()
    if not dry_run:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            while True:  # batches, worst offenders first, re-evaluated after each: stop once every section meets the targets
                batch = length_candidates(qs, skip=tried)[:max(1, workers)]
                if not batch:
                    break
                tried.update(batch)
                for i, (new, why) in zip(batch, ex.map(lambda i: fix_length(qs[i], chk, providers), batch)):
                    if new is not None:
                        qs[i] = new
                        changed.add(i)
                        rep["length_fixed"] += 1
                    else:
                        rep["length_failed"][why[:60]] = rep["length_failed"].get(why[:60], 0) + 1
                    progress(f"length item {i}: {'rewritten' if new is not None else 'kept original'}")
    before = {i: (q.correct_answer, q.explanation) for i, q in qs.items()}
    rep["position"] = balance_positions(qs, seed=seed)
    changed |= {i for i, q in qs.items() if before[i] != (q.correct_answer, q.explanation)}
    rep["changed_items"] = len(changed)
    rep["after"] = key_stats(qs.values())
    rep["length_after"] = length_stats(qs.values())
    if dry_run:
        return rep
    with file_lock(out):
        cur = read_store(out)
        n = 1  # never overwrite an earlier backup: .pre_balance.bak, .pre_balance2.bak, ...
        while Path(f"{out}.pre_balance{n if n > 1 else ''}.bak").exists():
            n += 1
        shutil.copy2(out, f"{out}.pre_balance{n if n > 1 else ''}.bak")
        rep["applied"] = 0
        for i in changed:
            if cur.get(i) == snap.get(i):  # skip items modified by someone else since the snapshot
                cur[i] = qs[i].model_dump()
                rep["applied"] += 1
        write_store(out, cur)
    rep["audit"] = audit_session(sid, output_dir)
    return rep


def format_report(r: dict) -> str:
    p = r["position"]
    return (f"balance {r['session_id']}{' (dry-run)' if r['dry_run'] else ''}: length-biased {r['length_biased']}, rewritten "
            f"{r['length_fixed']}, failed {r['length_failed']}; positions fixable {p['fixable']}/{p['mcq']}, fixed {p['fixed']}, "
            f"unfixable(reference) {p['unfixable_reference']}, unfixable(explanation) {p['unfixable_explanation']}; "
            f"items changed {r['changed_items']}\n  keys before {r['before']['overall']} after {r['after']['overall']}"
            f"\n  per section after {r['after']['sections']}"
            + "".join(f"\n  length[{k}] longest {v['longest']}/{v['mcq']} within20 {v['within']}/{v['nonnumeric']}" for k, v in r.get("length_after", {}).items())
            + (f"\n  audit {r['audit']['failed']}/{r['audit']['total']} failed {r['audit']['by_check']}" if "audit" in r else ""))
