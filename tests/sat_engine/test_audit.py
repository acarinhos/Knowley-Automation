import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from sat_engine.__main__ import main
from sat_engine.pipeline import importer, planner, session, verify
from sat_engine.pipeline.originality import OriginalityChecker, shingles
from sat_engine.progress import Progress
from sat_engine.schema import SATQuestion


def mq(options, key="A", answer_type="mcq", correct=None):
    return SimpleNamespace(answer_type=answer_type, options=options, correct_answer=correct or key)


# ---- numeric comparison
def test_integer_keys_are_exact_other_keys_use_1e6():
    assert verify.same_number("12", "12") and verify.same_number("12.0", "12") and verify.same_number("24/2", "12")
    assert not verify.same_number("12.0005", "12") and not verify.same_number("12.001", "12")  # old tol 1e-3 let these pass
    assert verify.same_number("0.3333333", "1/3") and not verify.same_number("0.333", "1/3")
    assert verify.same_number(r"\frac{3}{4}", "0.75") and verify.same_number(r"-\dfrac{1}{2}", "-1/2")
    assert not verify.same_number("abc", "abc")


def test_math_mcq_letter_must_agree_with_answer():
    q = mq({"A": "5", "B": "7", "C": "9", "D": "11"}, key="B")
    assert verify.math_ok(q, {"answer": "7", "choice": "B"})
    assert not verify.math_ok(q, {"answer": "9", "choice": "B"})  # right letter, contradicting value
    assert not verify.math_ok(q, {"answer": "7", "choice": "C"})
    assert verify.math_ok(q, {"answer": "7", "choice": None}) and not verify.math_ok(q, {"answer": "5"})
    assert not verify.math_ok(q, {"answer": None, "choice": None})
    assert verify.math_ok(mq(None, answer_type="grid_in", correct="2/3"), {"answer": "0.6666667"})


def test_duplicate_numeric_options_detected():
    assert verify.duplicate_numeric_options({"A": "0.5", "B": r"\frac{1}{2}", "C": "3", "D": "4"})
    assert verify.duplicate_numeric_options({"A": "1/2", "B": "2/4", "C": "3", "D": "4"})
    assert not verify.duplicate_numeric_options({"A": "1/2", "B": "1/3", "C": "x+1", "D": "x+1.5"})


def test_verify_rejects_numerically_equal_math_options(monkeypatch):
    from sat_engine import llm
    from tests.sat_engine.test_pipeline import fake_llm, slot
    from sat_engine.pipeline import generator
    monkeypatch.setattr(llm, "generate_json", fake_llm())
    q = generator.generate(slot("math"))
    q.options = {"A": "0.5", "B": "1/2", "C": "3", "D": "4"}
    q.correct_answer = "C"
    assert verify.verify(q) == (False, "math options are numerically equal")


def test_math_solver_excludes_generator_provider(monkeypatch):
    from sat_engine import llm
    from tests.sat_engine.test_pipeline import fake_llm, slot
    from sat_engine.pipeline import generator
    seen = []
    base = fake_llm()
    monkeypatch.setattr(llm, "generate_json", lambda p, **k: seen.append(k.get("exclude")) or base(p, **k))
    q = generator.generate(slot("math"))
    q.meta.provider = "groq"
    verify.verify(q)
    assert seen[-1] == ["groq"]


# ---- file lock / store
def test_lock_records_owner_and_only_owner_removes(tmp_path):
    f = tmp_path / "s.json"
    lk = tmp_path / "s.json.lock"
    with session.file_lock(f):
        d = json.loads(lk.read_text())
        assert d["pid"] == os.getpid() and d["token"]
        lk.write_text(json.dumps({"pid": os.getpid(), "token": "someone-else"}))  # lock stolen/replaced meanwhile
    assert lk.exists()  # not ours any more: left alone


def test_stale_lock_rules(tmp_path):
    f = tmp_path / "s.json"
    lk = tmp_path / "s.json.lock"
    lk.write_text(json.dumps({"pid": os.getpid(), "token": "t"}))  # live owner, fresh -> must wait, then time out
    with pytest.raises(TimeoutError):
        with session.file_lock(f, timeout=0.3):
            pass
    assert lk.exists()
    old = time.time() - 300  # live pid but older than 120 s -> stale
    os.utime(lk, (old, old))
    with session.file_lock(f, timeout=1):
        assert json.loads(lk.read_text())["token"] != "t"
    assert not lk.exists()
    lk.write_text(json.dumps({"pid": 2**22 + 12345, "token": "dead"}))  # dead owner, fresh -> broken at once
    assert not session._pid_alive(2**22 + 12345)
    with session.file_lock(f, timeout=1):
        pass


def test_store_retries_permission_errors(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("busy")
        return real(a, b)
    monkeypatch.setattr(session.os, "replace", flaky)
    monkeypatch.setattr(session.time, "sleep", lambda s: None)
    session.write_store(f, {"1": {}})
    assert len(calls) == 3 and session.read_store(f) == {"1": {}}


# ---- progress sync
def test_external_fills_do_not_exceed_target(tmp_path):
    p = Progress(None, targets={"rw": 2}, echo=False, heartbeat=0)
    for _ in range(5):
        p.bump("rw")
    p.bump("math")  # not a target section
    assert p.done["rw"] == 2 and p.done.get("math", 0) == 0


# ---- importer difficulty tiers
def _slot(diff):
    return SimpleNamespace(section="rw", domain="d", skill="k", needs_visual=False, blueprint={}, module=1, difficulty=diff, index=0)


def test_import_never_places_hard_in_easy_slot():
    q = SimpleNamespace(section="rw", domain="d", skill="k", visual=None, answer_type="mcq", module=1, difficulty="hard")
    assert importer._find_slot(q, [_slot("easy")]) == (None, False)
    s, loose = importer._find_slot(q, [_slot("easy"), _slot("medium")])
    assert s.difficulty == "medium" and loose
    q.difficulty = "easy"
    assert importer._find_slot(q, [_slot("hard")]) == (None, False)


# ---- audit
def _q(n, **kw):
    d = {"id": f"x{n}", "section": "rw", "module": 1, "difficulty": "easy", "domain": "Craft and Structure", "skill": "Words in Context",
         "passage": f"alpha{n} beta{n} gamma{n} delta{n} epsilon{n} zeta{n} eta{n} theta{n}", "stem": f"stem{n}?",
         "answer_type": "mcq", "options": {"A": f"a{n}", "B": f"b{n}", "C": f"c{n}", "D": f"d{n}"}, "correct_answer": "B", "explanation": "e"}
    return {**d, **kw}


def test_audit_reports_counts_and_drop_failed_makes_open_slots(tmp_path, monkeypatch, capsys):
    ref = "ancient river deltas shaped early farming settlements across distant valleys today"
    chk = OriginalityChecker(refs=[set(shingles(ref))])
    math = _q(2, section="math", domain="Algebra", skill="Linear functions", passage=None, stem="Pick one?",
              options={"A": "0.5", "B": "1/2", "C": "3", "D": "4"}, correct_answer="C", answer_type="mcq")
    store = {"0": _q(0), "1": _q(1, passage=ref), "2": math, "3": _q(0, id="dup")}  # slot 3 repeats slot 0 verbatim
    sid = "seed5-rw-math"
    f = tmp_path / f"{sid}.json"
    f.write_text(json.dumps(store), encoding="utf-8")
    before = f.read_bytes()
    monkeypatch.setattr(session.originality, "OriginalityChecker", lambda: chk)
    r = session.audit_session(sid, tmp_path)
    assert r["total"] == 4 and r["by_check"] == {"originality": 2, "numeric_options": 1} and r["failed"] == 3 and r["dropped"] == 0
    assert f.read_bytes() == before and json.loads((tmp_path / f"{sid}.audit.json").read_text())["failed"] == 3
    chk.sess.clear(); chk._sall.clear(); chk._seen.clear()
    r = session.audit_session(sid, tmp_path, drop_failed=True)
    assert r["dropped"] == 3 and (tmp_path / f"{sid}.json.bak").read_bytes() == before
    assert list(json.loads(f.read_text())) == ["0"] and not (tmp_path / f"{sid}.json.lock").exists()
    assert 1 in {s["index"] for s in session.open_slots(sid, tmp_path, blueprints=[])}
    # CLI wiring: no LLM, counts only
    f.write_bytes(before)
    chk.sess.clear(); chk._sall.clear(); chk._seen.clear()
    assert main(["--audit", "--session", sid, "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "3/4 failed" in out and "ancient" not in out
