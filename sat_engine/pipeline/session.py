"""Orchestrates planner -> generator -> verify -> originality -> optional store, resumable."""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..progress import Progress
from ..schema import SATQuestion
from . import generator, originality, planner, verify

log = logging.getLogger("sat_engine.pipeline")
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
MAX_TRIES = 3


def run_session(sections=("rw", "math"), seed=None, limit=None, providers=None, dry_run=True,
                output_dir: Path = OUTPUT_DIR, blueprints=None, store=None, checker=None, workers=10, progress=None) -> dict:
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
    lock = threading.Lock()  # guards checker, accepted, report counters and the output file

    def work(s):
        q, why = None, ""
        for attempt in range(MAX_TRIES):
            t0 = time.monotonic()
            try:
                cand = generator.generate(s, providers, qid=f"{sid}-{s.index}", feedback=why or None)
                ok, why = verify.verify(cand, providers)
                if ok:
                    with lock:  # check + register atomically so concurrent near-duplicates cannot both pass
                        ok, score = checker.check(cand)
                        why = "" if ok else "originality: too similar to existing text"
                        cand.meta.originality_score = score
                        if ok:
                            checker.add(cand)
            except Exception as e:  # noqa: BLE001 - LLM/spec/schema errors all mean "retry"
                ok, why = False, f"{type(e).__name__}: {str(e)[:300]}"
            if ok:
                q = cand
                break
            with lock:
                report["rejections"][why[:80]] += 1
            progress.event(s, "REJ", secs=time.monotonic() - t0, note=why)
            log.info("slot %s attempt %s rejected: %s", s.index, attempt + 1, why)
        with lock:
            if q is None:  # keep going: other slots must not be aborted; never pad with unverified items
                report["failures"].append({"slot": s.index, "section": s.section, "skill": s.skill, "reason": why})
                progress.event(s, "FAIL", note=why)
                return
            accepted[s.index] = q
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({str(i): x.model_dump() for i, x in accepted.items()}), encoding="utf-8")
            log.info("%s accepted slot %s (%d total)", s.section, s.index, len(accepted))
            progress.event(s, "OK", q.meta.provider or "", time.monotonic() - t0)

    slots_by_sec = {sec: [s for s in plan if s.section == sec][:limit] for sec in sections}
    progress = progress or Progress(Path(output_dir) / "progress.json", echo=False, heartbeat=0)
    progress.targets = {sec: len(sl) for sec, sl in slots_by_sec.items()}
    progress.done = {sec: sum(1 for s in sl if s.index in accepted) for sec, sl in slots_by_sec.items()}
    progress.start_done = sum(progress.done.values())
    todo = [s for sl in slots_by_sec.values() for s in sl if s.index not in accepted]
    with progress, ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for f in [ex.submit(work, s) for s in todo]:
            f.result()
    report["failures"].sort(key=lambda f: f["slot"])
    for sec, slots in slots_by_sec.items():
        n = sum(1 for s in slots if s.index in accepted)
        report["sections"][sec] = {"target": len(slots), "accepted": n, "complete": n == len(slots)}
        report["ok"] &= n == len(slots)
    qs = [accepted[i] for i in sorted(accepted) if plan[i].section in sections]
    if store is None:
        from .. import store
    report["store"] = store.save_questions(qs, dry_run=dry_run, session_id=sid)
    report["rejections"] = dict(report["rejections"].most_common())
    if report["rejections"]:
        log.info("rejections by reason: %s", report["rejections"])
    report["output"] = str(out)
    return report
