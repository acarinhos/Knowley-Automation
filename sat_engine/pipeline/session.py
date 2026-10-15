"""Orchestrates planner -> generator -> verify -> originality -> optional store, resumable."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from ..progress import Progress
from ..schema import SATQuestion
from . import generator, originality, planner, verify

log = logging.getLogger("sat_engine.pipeline")
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
MAX_TRIES = 3


def _why(e: Exception) -> str:
    """Short rejection reason; for schema errors name the offending field and message."""
    if isinstance(e, ValidationError):
        return f"schema {e.title}: " + "; ".join(f"{'.'.join(map(str, x['loc'])) or 'model'}: {x['msg'][:60]}" for x in e.errors()[:2])
    return f"{type(e).__name__}: {str(e)[:700]}"


STALE_LOCK_S = 120


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) would TERMINATE the process on Windows
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return k.GetLastError() == 5  # access denied: exists
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _lock_owner(lk: Path) -> tuple[str, dict]:
    try:
        raw = lk.read_text(encoding="utf-8")
    except OSError:
        return "", {}
    try:
        d = json.loads(raw)
        return raw, d if isinstance(d, dict) else {}
    except ValueError:
        return raw, {}  # empty/legacy lock without pid: only the age rule applies


def _lock_is_stale(lk: Path, d: dict) -> bool:
    try:
        age = time.time() - lk.stat().st_mtime
    except OSError:
        return False
    pid = d.get("pid")
    return age > STALE_LOCK_S or (isinstance(pid, int) and not _pid_alive(pid))


@contextmanager
def file_lock(path, timeout=150):
    """Cross-process lock (session, importer and audit all rewrite the same output file). The lock file holds
    {pid, token}; only the owner removes it, and a foreign lock is broken only if its pid is dead or it is > 120 s old."""
    lk = Path(str(path) + ".lock")
    token = uuid.uuid4().hex
    t0 = time.monotonic()
    while True:
        try:
            fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "token": token}, f)
            break
        except FileExistsError:
            raw, d = _lock_owner(lk)
            if _lock_is_stale(lk, d) and _lock_owner(lk)[0] == raw:  # still the same stale owner
                lk.unlink(missing_ok=True)
            elif time.monotonic() - t0 > timeout:
                raise TimeoutError(f"lock {lk.name} is held by a live process")
            else:
                time.sleep(0.05)
        except PermissionError:  # Windows: lock being deleted by its owner
            time.sleep(0.05)
    try:
        yield
    finally:
        if _lock_owner(lk)[1].get("token") == token:
            lk.unlink(missing_ok=True)


def _retry_win(fn, tries=20):
    for i in range(tries):
        try:
            return fn()
        except PermissionError:  # Windows: another process holds the file for a moment
            if i == tries - 1:
                raise
            time.sleep(0.05 * (i + 1))


def read_store(out: Path) -> dict:
    out = Path(out)
    return _retry_win(lambda: json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {})


def write_store(out: Path, data: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    _retry_win(lambda: os.replace(tmp, out))


def parse_sid(sid: str) -> tuple[int, tuple]:
    m = re.fullmatch(r"seed(\d+)-(rw|math)(?:-(rw|math))?", sid)
    if not m:
        raise ValueError(f"bad session id {sid!r} (expected seed<N>-rw[-math])")
    return int(m[1]), tuple(g for g in m.groups()[1:] if g)


def open_slots(sid: str, output_dir: Path = OUTPUT_DIR, blueprints=None) -> list[dict]:
    seed, secs = parse_sid(sid)
    done = read_store(Path(output_dir) / f"{sid}.json")
    return [{"index": s.index, "section": s.section, "module": s.module, "domain": s.domain, "skill": s.skill,
             "difficulty": s.difficulty, "needs_visual": s.needs_visual, "answer_type": s.blueprint.get("answer_type", "mcq")}
            for s in planner.build_plan(seed, blueprints, secs) if str(s.index) not in done]


def run_session(sections=("rw", "math"), seed=None, limit=None, providers=None, dry_run=True,
                output_dir: Path = OUTPUT_DIR, blueprints=None, store=None, checker=None, workers=10, progress=None) -> dict:
    seed = random.SystemRandom().randrange(10**6) if seed is None else int(seed)
    sid = f"seed{seed}-{'-'.join(sections)}"
    out = Path(output_dir) / f"{sid}.json"
    done = read_store(out)  # resume: slot index -> accepted question dict
    plan = planner.build_plan(seed, blueprints, sections)
    checker = checker or originality.OriginalityChecker()
    accepted: dict[int, SATQuestion] = {int(i): SATQuestion.model_validate(d) for i, d in done.items()}
    for q in accepted.values():
        checker.add(q)
    report = {"session_id": sid, "seed": seed, "sections": {}, "failures": [], "rejections": Counter(), "ok": True}
    lock = threading.Lock()  # guards checker, accepted, report counters and the output file

    def sync():
        """Adopt questions written to the output file by other processes (the importer). Caller holds `lock`."""
        for i, d in read_store(out).items():
            if int(i) not in accepted:
                q = SATQuestion.model_validate(d)
                accepted[int(i)] = q
                checker.add(q)
                if int(i) in target_slots:
                    progress.bump(plan[int(i)].section)

    def work(s):
        q, why, note = None, "", ""
        with lock:
            sync()
            if s.index in accepted:
                return
        for attempt in range(MAX_TRIES):
            t0, note = time.monotonic(), ""
            try:
                cand = generator.generate(s, providers, qid=f"{sid}-{s.index}", feedback=why or None)
                ok, why = verify.verify(cand, providers)
                if ok:
                    with lock:  # check + register atomically so concurrent near-duplicates cannot both pass
                        ok, score = checker.check(cand)
                        why = "" if ok else "originality: too similar to existing text"
                        note = "" if ok else f"{why} [{checker.explain(cand)}]"
                        cand.meta.originality_score = score
                        if ok:
                            checker.add(cand)
            except Exception as e:  # noqa: BLE001 - LLM/spec/schema errors all mean "retry"
                ok, why = False, _why(e)
                note = ""
            if ok:
                q = cand
                break
            with lock:
                report["rejections"][why[:80]] += 1
            progress.event(s, "REJ", secs=time.monotonic() - t0, note=note or why)
            log.info("slot %s attempt %s rejected: %s", s.index, attempt + 1, why)
        with lock:
            if q is None:  # keep going: other slots must not be aborted; never pad with unverified items
                report["failures"].append({"slot": s.index, "section": s.section, "skill": s.skill, "reason": why})
                progress.event(s, "FAIL", note=why)
                return
            with file_lock(out):
                sync()
                if s.index in accepted:  # filled externally while we were generating
                    return
                accepted[s.index] = q
                write_store(out, {str(i): x.model_dump() for i, x in accepted.items()})
            log.info("%s accepted slot %s (%d total)", s.section, s.index, len(accepted))
            progress.event(s, "OK", q.meta.provider or "", time.monotonic() - t0)

    slots_by_sec = {sec: [s for s in plan if s.section == sec][:limit] for sec in sections}
    progress = progress or Progress(Path(output_dir) / "progress.json", echo=False, heartbeat=0)
    progress.targets = {sec: len(sl) for sec, sl in slots_by_sec.items()}
    progress.done = {sec: sum(1 for s in sl if s.index in accepted) for sec, sl in slots_by_sec.items()}
    progress.start_done = sum(progress.done.values())
    target_slots = {s.index for sl in slots_by_sec.values() for s in sl}
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


def audit_session(sid: str, output_dir: Path = OUTPUT_DIR, drop_failed: bool = False, checker=None) -> dict:
    """Offline (no LLM) re-check of every accepted question with the current originality and numeric-option rules.
    Writes <output_dir>/<sid>.audit.json; with drop_failed, failing slots are removed from the store under the file lock
    (a copy of the previous store stays in <file>.bak) so they become open slots. Returns counts only."""
    out = Path(output_dir) / f"{sid}.json"

    def run() -> dict:
        store = read_store(out)
        chk = checker or originality.OriginalityChecker()
        fails: dict[str, list[str]] = {}
        for i in sorted(store, key=int):
            try:
                q = SATQuestion.model_validate(store[i])
            except ValueError:
                fails.setdefault("schema", []).append(i)
                continue
            bad = False
            if q.section == "math" and q.answer_type == "mcq" and verify.duplicate_numeric_options(q.options):
                fails.setdefault("numeric_options", []).append(i)
                bad = True
            if not chk.check(q)[0]:
                fails.setdefault("originality", []).append(i)
                bad = True
            if not bad:
                chk.add(q)  # only passing questions count as earlier session text
        failed = sorted({i for v in fails.values() for i in v}, key=int)
        rep = {"session_id": sid, "total": len(store), "failed": len(failed), "passed": len(store) - len(failed),
               "by_check": {k: len(v) for k, v in fails.items()}, "failed_slots": failed, "dropped": 0}
        if drop_failed and failed:
            shutil.copy2(out, str(out) + ".bak")
            write_store(out, {i: d for i, d in store.items() if i not in set(failed)})
            rep["dropped"] = len(failed)
        (Path(output_dir) / f"{sid}.audit.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
        return rep

    if drop_failed:
        with file_lock(out):
            return run()
    return run()
