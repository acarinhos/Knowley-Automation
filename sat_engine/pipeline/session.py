"""Orchestrates planner -> generator -> verify -> originality -> optional store, resumable."""
from __future__ import annotations

import json
import logging
import random
from collections import Counter
from pathlib import Path

from ..schema import SATQuestion
from . import generator, originality, planner, verify

log = logging.getLogger("sat_engine.pipeline")
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
MAX_TRIES = 3


def run_session(sections=("rw", "math"), seed=None, limit=None, providers=None, dry_run=True,
                output_dir: Path = OUTPUT_DIR, blueprints=None, store=None, checker=None) -> dict:
    seed = random.SystemRandom().randrange(10**6) if seed is None else int(seed)
    sid = f"seed{seed}-{'-'.join(sections)}"
    out = Path(output_dir) / f"{sid}.json"
    done: dict[str, dict] = {}
    if out.is_file():  # resume: slot index -> accepted question dict
        done = json.loads(out.read_text(encoding="utf-8"))
    plan = planner.build_plan(seed, blueprints, sections)
    checker = checker or originality.OriginalityChecker()
    accepted: dict[int, SATQuestion] = {int(i): SATQuestion.model_validate(d) for i, d in done.items()}
    for q in accepted.values():
        checker.add(q)
    report = {"session_id": sid, "seed": seed, "sections": {}, "failures": [], "rejections": Counter(), "ok": True}
    for sec in sections:
        slots = [s for s in plan if s.section == sec][:limit]
        failed = False
        for s in slots:
            if s.index in accepted:
                continue
            q, why = None, ""
            for attempt in range(MAX_TRIES):
                try:
                    cand = generator.generate(s, providers, qid=f"{sid}-{s.index}", feedback=why or None)
                    ok, why = verify.verify(cand, providers)
                    if ok:
                        ok, score = checker.check(cand)
                        why = "" if ok else "originality: too similar to existing text"
                        cand.meta.originality_score = score
                except Exception as e:  # noqa: BLE001 - LLM/spec/schema errors all mean "retry"
                    ok, why = False, f"{type(e).__name__}: {str(e)[:300]}"
                if ok:
                    q = cand
                    break
                report["rejections"][why[:80]] += 1
                log.info("slot %s attempt %s rejected: %s", s.index, attempt + 1, why)
            if q is None:
                report["failures"].append({"slot": s.index, "section": sec, "skill": s.skill, "reason": why})
                failed = True
                break  # never pad with unverified items
            accepted[s.index] = q
            checker.add(q)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({str(i): x.model_dump() for i, x in accepted.items()}), encoding="utf-8")
            log.info("%s %d/%d accepted", sec, sum(1 for i in accepted if plan[i].section == sec), len(slots))
        n = sum(1 for s in slots if s.index in accepted)
        report["sections"][sec] = {"target": len(slots), "accepted": n, "complete": n == len(slots) and not failed}
        report["ok"] &= report["sections"][sec]["complete"]
    qs = [accepted[i] for i in sorted(accepted) if plan[i].section in sections]
    if store is None:
        from .. import store
    report["store"] = store.save_questions(qs, dry_run=dry_run, session_id=sid)
    report["rejections"] = dict(report["rejections"].most_common())
    if report["rejections"]:
        log.info("rejections by reason: %s", report["rejections"])
    report["output"] = str(out)
    return report
