"""Merge externally authored questions (SATQuestion-shaped dicts) into a session output file.

Same gates as generated items: schema, figure spec, independent verification, originality. Accepted items fill the
next open slot of the matching section/module/domain/skill/difficulty; no padding, rejects are reported with reasons.
"""
from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..schema import SATQuestion
from ..visuals import parse_spec, render_svg
from . import generator, originality, planner, session, verify

_LEVEL = {"easy": 0, "medium": 1, "hard": 2}
TIERS = (  # progressively looser slot predicates (visual / grid-in shape must always match)
    lambda q, s: (q.module, q.difficulty) == (s.module, s.difficulty),
    lambda q, s: q.difficulty == s.difficulty,
    lambda q, s: abs(_LEVEL[q.difficulty] - _LEVEL[s.difficulty]) <= 1,  # loose: never hard<->easy
)


def _prepare(d: dict) -> SATQuestion:
    d = dict(d)
    d.setdefault("id", uuid.uuid4().hex)
    fig, vals = d.pop("figure", None), d.pop("figure_values", None)
    vis = d.get("visual")
    if isinstance(vis, dict) and vis.get("spec"):
        fig = fig or vis["spec"]
    if fig:
        spec = generator.normalize_spec(generator.coerce_spec(fig))
        parse_spec(spec)
        generator.check_facts(vals, spec)
        d["visual"] = {"spec": spec, "svg": render_svg(spec)}
    meta = dict(d.get("meta") or {})
    meta.setdefault("provider", "external")
    meta.setdefault("model", "external")
    d["meta"] = meta
    return SATQuestion.model_validate(d)


def _find_slot(q, open_):
    for i, tier in enumerate(TIERS):
        for s in open_:
            if (s.section == q.section and s.domain == q.domain and s.skill == q.skill
                    and bool(s.needs_visual) == bool(q.visual) and (s.blueprint.get("answer_type") == "grid_in") == (q.answer_type == "grid_in")
                    and tier(q, s)):
                return s, i > 1
    return None, False


def import_questions(path, sid, providers=None, output_dir=session.OUTPUT_DIR, blueprints=None, checker=None, workers=4) -> dict:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("questions") or list(items.values())
    seed, secs = session.parse_sid(sid)
    out = Path(output_dir) / f"{sid}.json"
    plan = planner.build_plan(seed, blueprints, secs)
    checker = checker or originality.OriginalityChecker()
    rejected, placed = [], []

    def gate(item):
        try:
            q = _prepare(item)
            ok, why = verify.verify(q, providers)
            return q, "" if ok else why
        except Exception as e:  # noqa: BLE001
            return None, session._why(e)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        gated = list(ex.map(gate, items))
    with session.file_lock(out):  # also guards against a running session writing the same file
        store = session.read_store(out)
        for i in store:
            checker.add(SATQuestion.model_validate(store[i]))
        open_ = [s for s in plan if str(s.index) not in store]
        for n, (q, why) in enumerate(gated):
            if q is not None and not why:
                ok, score = checker.check(q)
                q.meta.originality_score = score
                why = "" if ok else f"originality: too similar to existing text [{checker.explain(q)}]"
            if q is None or why:
                rejected.append({"item": n, "reason": why})
                continue
            slot, loose = _find_slot(q, open_)
            if slot is None:
                rejected.append({"item": n, "reason": f"no open slot for {q.section}/{q.domain}/{q.skill}"})
                continue
            open_.remove(slot)
            q.id, q.module = f"{sid}-{slot.index}", slot.module
            checker.add(q)
            store[str(slot.index)] = q.model_dump()  # a loose match keeps its authored difficulty (not relabelled)
            placed.append({"item": n, "slot": slot.index, "loose": loose, "difficulty": q.difficulty, "slot_difficulty": slot.difficulty})
        session.write_store(out, store)
    return {"session_id": sid, "input": len(items), "accepted": len(placed), "placed": placed, "rejected": rejected,
            "open_left": len(open_), "output": str(out)}
