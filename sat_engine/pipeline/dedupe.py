"""Topic/scenario de-duplication of an accepted session: for every near-duplicate scenario pair keep the earlier item and
regenerate the later one for the SAME slot (section/module/domain/skill/difficulty/visual/grid shape, same key letter) with a
new topic, through the normal generator + verify + originality pipeline plus the scenario-similarity check against ALL items."""
from __future__ import annotations

import dataclasses
import json
import random
import shutil
import threading
import time
from pathlib import Path

from ..schema import SATQuestion
from . import balance, generator, originality, planner, similarity, verify
from .clean import next_backup
from .session import OUTPUT_DIR, audit_session, file_lock, parse_sid, read_store, write_store

ATTEMPTS = 3


def regen_set(pairs: list[tuple]) -> dict:
    """{later item: [kept earlier partners]}: an item is regenerated when it is similar to an earlier item that stays."""
    partners: dict = {}
    for p in pairs:
        a, b = sorted((p[1], p[2]), key=int)
        partners.setdefault(b, []).append(a)
    regen: dict = {}
    for b in sorted(partners, key=int):
        kept = [a for a in partners[b] if a not in regen]
        if kept:
            regen[b] = kept
    return regen


def pick_topics(avoid: set, qs: dict, ignore, used=(), seed=0) -> list[str]:
    """Topics of the 60-topic list ranked by how little their words appear in the other items (best first), without `avoid`."""
    vocab: dict = {}
    for k, q in qs.items():
        if k not in ignore:
            for w in set(similarity.tokens(similarity.content_text(q))):
                vocab[w] = vocab.get(w, 0) + 1
    cand = [t for t in planner.TOPICS if t not in avoid and t not in used]
    rng = random.Random(seed)
    rng.shuffle(cand)
    return sorted(cand, key=lambda t: sum(vocab.get(w, 0) for w in similarity.tokens(t)))


def slot_for(plan: list, idx: int, q: SATQuestion, topic: str):
    s = plan[idx]
    bp = dict(s.blueprint)
    if (s.section, s.domain, s.skill) != (q.section, q.domain, q.skill):  # externally imported item filling another slot
        bp = planner._pick_blueprint(planner.load_blueprints(), random.Random(idx), q.section, q.domain, q.skill, q.difficulty, q.visual is not None)
    bp["answer_type"] = q.answer_type
    return dataclasses.replace(
        s, section=q.section, module=q.module, domain=q.domain, skill=q.skill, difficulty=q.difficulty, needs_visual=q.visual is not None,
        blueprint=bp, topic=topic, numbers=tuple(random.Random(f"{idx}-{topic}").sample(range(2, 98), 3)),
        target_key_letter=q.correct_answer if q.answer_type == "mcq" else "")


def _same_shape(c: SATQuestion, old: SATQuestion) -> str:
    if (c.section, c.module, c.domain, c.skill, c.difficulty, c.answer_type) != (old.section, old.module, old.domain, old.skill, old.difficulty, old.answer_type):
        return "slot shape changed"
    if (c.visual is None) != (old.visual is None):
        return "visual presence changed"
    return ""


def dedupe_session(sid: str, output_dir: Path = OUTPUT_DIR, dry_run: bool = False, providers=None, workers: int = 3,
                   progress=print, threshold: float = similarity.THRESHOLD, attempts: int = ATTEMPTS, checker_factory=None) -> dict:
    out = Path(output_dir) / f"{sid}.json"
    snap = read_store(out)
    qs = {i: SATQuestion.model_validate(d) for i, d in snap.items()}
    seed, secs = parse_sid(sid)
    plan = planner.build_plan(seed, None, secs)
    pairs = similarity.similar_pairs(qs, threshold)
    regen = regen_set(pairs)
    rep = {"session_id": sid, "dry_run": dry_run, "threshold": threshold, "min_shared_terms": similarity.MIN_SHARED, "pairs": len(pairs),
           "clusters": [list(g) for g in similarity.clusters(pairs)], "pair_list": [{"a": p[1], "b": p[2], "cosine": p[0], "shared_terms": p[3], "entities": p[4]} for p in pairs],
           "to_regenerate": sorted(regen, key=int), "regenerated": {}, "kept_original": {}, "key_before": {i: qs[i].correct_answer for i in regen}}
    if dry_run or not regen:
        return rep
    current = dict(qs)
    used_topics: set = set()
    lock = threading.Lock()
    state = {"backup": None}
    factory = checker_factory or originality.OriginalityChecker

    def merge(i, new):
        with file_lock(out):
            cur = read_store(out)
            if cur.get(i) != snap.get(i):
                return False  # somebody else changed this slot meanwhile
            if state["backup"] is None:
                state["backup"] = str(shutil.copy2(out, next_backup(out)))
            cur[i] = new.model_dump()
            write_store(out, cur)
        return True

    def attempt(i, old, topic):
        """One generation try -> (candidate, chk, '') or (None, None, reason)."""
        cand = generator.generate(slot_for(plan, int(i), old, topic), providers, qid=f"{sid}-{i}")
        why = _same_shape(cand, old)
        if not why and cand.answer_type == "mcq" and cand.correct_answer != old.correct_answer:  # keep the key-letter balance
            if balance.shuffle_class(cand) is None or not balance._apply(cand, old.correct_answer):
                why = "could not move key to the required letter"
        if why:
            return None, None, why
        ok, why = verify.verify(cand, providers)
        if not ok:
            return None, None, why
        with lock:  # check + reserve atomically against everything else (incl. items regenerated meanwhile)
            others = {k: q for k, q in current.items() if k != i}
            chk = factory()
            for q in others.values():
                chk.add(q)
            ok, cand.meta.originality_score = chk.check(cand)
            if not ok:
                return None, None, "originality: too similar to existing text"
            sim, cos, other, _ = similarity.check_candidate(cand, others, threshold=threshold)
            if sim:
                return None, None, f"scenario too similar to item {other} (cos {cos:.2f})"
            current[i] = cand
        return cand, chk, ""

    def work(i):
        old, why = qs[i], "no attempt"
        avoid = {plan[int(k)].topic for k in regen[i]} | {plan[int(i)].topic}
        with lock:
            order = pick_topics(avoid, current, {i}, used_topics, seed=int(i))
        for n in range(attempts):
            topic, t0 = order[n], time.monotonic()
            try:
                cand, chk, why = attempt(i, old, topic)
                if cand is not None:
                    if cand.answer_type == "mcq" and not balance.numeric_options(cand.options) and not balance.length_ok(cand.options, cand.correct_answer):
                        cand = balance.fix_length(cand, chk, providers, tries=2)[0] or cand  # else keep: verified original beats a biased original
                    with lock:
                        current[i] = cand
                    if merge(i, cand):
                        used_topics.add(topic)
                        rep["regenerated"][i] = {"topic": topic, "attempt": n + 1, "provider": cand.meta.provider}
                        progress(f"{time.strftime('%H:%M:%S')} item {i}: regenerated on topic {topic!r} (attempt {n + 1})")
                        return
                    with lock:
                        current[i] = old
                    why = "slot changed externally"
                    break
            except Exception as e:  # noqa: BLE001 - LLM/schema errors mean "retry"
                why = f"{type(e).__name__}: {str(e)[:120]}"
            progress(f"{time.strftime('%H:%M:%S')} item {i}: attempt {n + 1} rejected after {time.monotonic() - t0:.0f}s: {why[:150]}")
        rep["kept_original"][i] = why[:150]
        progress(f"{time.strftime('%H:%M:%S')} item {i}: kept original ({why[:100]})")

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for f in [ex.submit(work, i) for i in sorted(regen, key=int)]:
            f.result()
    rep["backup"] = state["backup"]
    final = {i: SATQuestion.model_validate(d) for i, d in read_store(out).items()}
    rep["pairs_after"] = len(similarity.similar_pairs(final, threshold))
    rep["key_after"] = {i: final[i].correct_answer for i in regen}
    rep["key_stats"] = balance.key_stats(final.values())
    rep["length_stats"] = balance.length_stats(final.values())
    rep["audit"] = audit_session(sid, output_dir)
    (Path(output_dir) / f"{sid}.dedupe_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return rep


def format_report(r: dict) -> str:
    s = (f"dedupe {r['session_id']}{' (dry-run)' if r['dry_run'] else ''}: threshold cos>={r['threshold']} with >={r['min_shared_terms']} shared terms "
         f"(or >=2 rare entities at the lower bar), {r['pairs']} similar pairs in {len(r['clusters'])} clusters {r['clusters']}\n"
         f"  to regenerate {r['to_regenerate']}")
    if not r["dry_run"]:
        s += (f"\n  regenerated {sorted(r['regenerated'], key=int)}; kept original {r['kept_original']}"
              f"\n  key letters of regenerated before {r['key_before']} after {r.get('key_after')}"
              f"\n  pairs after {r.get('pairs_after')}; keys {r.get('key_stats', {}).get('overall')}; key longest {r.get('key_stats', {}).get('key_strictly_longest')}"
              f"\n  audit {r['audit']['failed']}/{r['audit']['total']} failed {r['audit']['by_check']}" if "audit" in r else "")
    return s
