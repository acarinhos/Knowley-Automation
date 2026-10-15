import itertools
import re
from types import SimpleNamespace

import pytest

from sat_engine import llm
from sat_engine.pipeline import generator, planner, session, verify
from sat_engine.pipeline.originality import OriginalityChecker, shingles
from sat_engine.schema import SATQuestion

FIG = {"kind": "geometry", "shape": "right_triangle", "a": 3, "b": 4, "hypotenuse": 5, "unknown": ["c"]}
_n = itertools.count()


def fake_llm(solver_choice="B", defensible=("B",), figure_values=None):
    """Mocked generate_json: routes by prompt type; every generated stem is unique."""
    def f(prompt, system=None, schema=None, providers=None, images=None, exclude=None):
        if "Evaluate EACH" in prompt:
            return {"options": {k: {"acceptable": k in defensible, "reason": "r", "rule": None if k in defensible else "subject-verb agreement"}
                                for k in "ABCD"}}
        if "Solve." in prompt:
            m = re.search(rf"^{solver_choice}\. (.*)$", prompt, re.M)  # answer consistent with the returned letter
            return {"answer": m[1] if m else "10", "choice": solver_choice}
        n = next(_n)
        d = {"passage": None, "stem": f"zq{n}a zq{n}b zq{n}c zq{n}d zq{n}e zq{n}f?",
             "options": {"A": f"p{n}", "B": f"q{n}", "C": f"r{n}", "D": f"s{n}"}, "correct_answer": "B", "explanation": "x"}
        if "figure_values" in prompt:
            d["figure"], d["figure_values"] = FIG, figure_values or {"hyp": 5}
        return d
    return f


class FakeStore:
    def __init__(self):
        self.calls = []

    def save_questions(self, qs, dry_run=True, session_id=None):
        self.calls.append((len(list(qs)), dry_run))
        return {"dry_run": dry_run}


def slot(section="rw", visual=False):
    dom, skill = ("Craft and Structure", "Words in Context") if section == "rw" else ("Geometry and Trigonometry", "Right triangles and trigonometry")
    return planner.Slot(0, section, 1, dom, skill, "easy", visual, {"id": "bp1", "answer_type": "mcq"})


def test_plan_counts_and_determinism():
    p = planner.build_plan(7, blueprints=[])
    key = lambda s, m: sum(1 for x in p if x.section == s and x.module == m)  # noqa: E731
    assert (key("rw", 1), key("rw", 2), key("math", 1), key("math", 2)) == (27, 27, 22, 22)
    assert len(p) == 98
    assert sum(s.needs_visual for s in p if s.section == "math") == 11
    assert not any(s.needs_visual for s in p if s.section == "rw")
    assert [(s.skill, s.difficulty) for s in p] == [(s.skill, s.difficulty) for s in planner.build_plan(7, blueprints=[])]
    hard = lambda m: sum(s.difficulty == "hard" for s in p if s.module == m)  # noqa: E731
    assert hard(2) > hard(1)


def test_originality_rejects_copied_ngram_run():
    ref = "the quick brown fox jumps over the lazy dog near the old river bank today"
    chk = OriginalityChecker(refs=[set(shingles(ref))])
    q = SimpleNamespace(passage=None, options={"A": "x"}, stem="Intro words: the quick brown fox jumps over the lazy dog")
    ok, score = chk.check(q)
    assert not ok and score < 1
    fresh = SimpleNamespace(passage=None, options=None, stem="Completely different sentence about planets and orbits tonight")
    assert chk.check(fresh) == (True, 1.0)


def test_verifier_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", fake_llm())
    q = generator.generate(slot("rw"))
    assert verify.verify(q) == (True, "")
    monkeypatch.setattr(llm, "generate_json", fake_llm(defensible=("C",)))
    assert not verify.verify(q)[0]
    monkeypatch.setattr(llm, "generate_json", fake_llm(defensible=("B", "D")))
    assert "exactly one acceptable" in verify.verify(q)[1]
    m = generator.generate(slot("math"))
    monkeypatch.setattr(llm, "generate_json", fake_llm(solver_choice="A"))
    assert not verify.verify(m)[0]


def test_numeric_compare():
    assert verify.same_number("0.5", "1/2") and verify.same_number("1,000", "1000") and not verify.same_number("3", "4")


def test_visual_path_renders_and_checks_facts(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", fake_llm())
    q = generator.generate(slot("math", visual=True))
    assert q.visual["svg"].count("<svg") == 1 and q.visual["spec"] == FIG
    monkeypatch.setattr(llm, "generate_json", fake_llm(figure_values={"hyp": 9}))
    with pytest.raises(ValueError):
        generator.generate(slot("math", visual=True))


def test_session_dry_run_and_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "generate_json", fake_llm())
    st = FakeStore()
    kw = dict(seed=3, limit=2, output_dir=tmp_path, blueprints=[], store=st, checker=OriginalityChecker(refs=[]))
    r = session.run_session(dry_run=True, **kw)
    assert r["ok"] and st.calls == [(4, True)]
    assert r["sections"]["rw"]["accepted"] == 2 and r["sections"]["math"]["accepted"] == 2
    SATQuestion.model_validate(next(iter(__import__("json").loads(open(r["output"], encoding="utf-8").read()).values())))
    calls = []
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: calls.append(1))  # resume must not call the LLM
    r2 = session.run_session(dry_run=False, **{**kw, "checker": OriginalityChecker(refs=[])})
    assert not calls and r2["ok"] and st.calls[-1] == (4, False)


def test_session_stops_when_unverifiable(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "generate_json", fake_llm(defensible=("D",)))
    r = session.run_session(sections=("rw",), seed=1, limit=3, output_dir=tmp_path, blueprints=[],
                            store=FakeStore(), checker=OriginalityChecker(refs=[]))
    assert not r["ok"] and r["sections"]["rw"]["accepted"] == 0 and len(r["failures"]) == 3


def test_meta_records_provider_and_model(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", fake_llm())
    monkeypatch.setattr(llm, "last_used", lambda: {"provider": "groq", "model": "m1"})
    q = generator.generate(slot("rw"))
    assert (q.meta.provider, q.meta.model) == ("groq", "m1")


def test_figure_as_json_string_is_accepted(monkeypatch):
    import json as _j
    base = fake_llm()
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: {**base(*a, **k), "figure": _j.dumps(FIG)})
    assert generator.generate(slot("math", visual=True)).visual["spec"] == FIG
    with pytest.raises(ValueError, match="not valid JSON"):
        generator.coerce_spec("{bad")
    assert generator.coerce_spec({"spec": FIG}) == FIG


def test_prompts_follow_digital_sat_style():
    rw = generator.build_prompt(slot("rw"))
    assert "NEVER refer to line numbers" in rw and "most logical and precise word or phrase" in rw
    m = generator.build_prompt(slot("math", visual=True))
    assert '"kind":"function_graph"' in m and "JSON OBJECT" in m and "Exactly" in m
    s = slot("math")
    s.blueprint = {"answer_type": "grid_in"}
    assert "student-produced" in generator.build_prompt(s)


def test_plan_has_grid_ins_for_math_only():
    p = planner.build_plan(7, blueprints=[])
    grid = [s for s in p if s.blueprint.get("answer_type") == "grid_in"]
    assert grid and all(s.section == "math" and not s.needs_visual for s in grid)
    assert 0.15 < len(grid) / sum(s.section == "math" for s in p) < 0.3


def test_session_reports_rejection_reasons(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "generate_json", fake_llm(defensible=("D",)))
    r = session.run_session(sections=("rw",), seed=1, limit=1, output_dir=tmp_path, blueprints=[],
                            store=FakeStore(), checker=OriginalityChecker(refs=[]))
    assert r["rejections"] == {"solver disagrees with key": session.MAX_TRIES}


def test_official_stem_boilerplate_is_not_flagged():
    stem = "Which choice completes the text so that it conforms to the conventions of Standard English?"
    chk = OriginalityChecker(refs=[set(shingles(stem))])
    q = SimpleNamespace(passage="Zebras graze at dusk", options={"A": "runs", "B": "run"}, stem=stem)
    assert chk.check(q)[0]


def test_conventions_requires_named_rules_and_records_them(monkeypatch):
    s = planner.Slot(0, "rw", 1, "Standard English Conventions", "Form, Structure, and Sense", "easy", False, {"id": "b", "answer_type": "mcq"})
    monkeypatch.setattr(llm, "generate_json", fake_llm())
    q = generator.generate(s)
    assert verify.verify(q) == (True, "") and "Distractor rules: A: subject-verb agreement" in q.explanation
    base = fake_llm()
    def no_rule(prompt, **k):
        r = base(prompt, **k)
        if "Evaluate EACH" in prompt:
            r["options"]["A"]["rule"] = None
        return r
    monkeypatch.setattr(llm, "generate_json", no_rule)
    assert "named" in verify.verify(generator.generate(s))[1]


def test_judge_prefers_other_provider():
    q = SimpleNamespace(meta=SimpleNamespace(provider="gemini"))
    assert verify.judge_providers(q)[0] == "groq" and verify.judge_providers(q)[-1] == "gemini"
    assert verify.judge_providers(q, ["gemini"]) == ["gemini"]


def test_null_strings_and_typography_normalized():
    base = dict(id="x", section="math", module=1, difficulty="easy", domain="Algebra", skill="Linear functions",
                stem="Find x‑intercept of “f” now​ − 3", passage="None", answer_type="grid_in",
                options="null", correct_answer="3", explanation="Solve: x − 3 = 0 so x–3 is 3.")
    q = SATQuestion.model_validate(base)
    assert q.passage is None and q.options is None
    assert q.stem == 'Find x-intercept of "f" now − 3' and "x-3" in q.explanation
    r = SATQuestion.model_validate({**base, "section": "rw", "domain": "Craft and Structure", "skill": "Words in Context",
                                    "answer_type": "mcq", "options": {"A": "a−", "B": "b", "C": "c", "D": "d"}, "correct_answer": "A"})
    assert r.options["A"] == "a-" and "−" not in r.stem
    with pytest.raises(ValueError, match="answer choices"):
        SATQuestion.model_validate({**base, "explanation": "The wrong choices are 4 and 5"})


def test_parallel_session_keeps_going_and_orders(monkeypatch, tmp_path):
    base = fake_llm()
    def flaky(prompt, **k):
        if "Evaluate EACH" in prompt and "zq" in prompt and flaky.bad(prompt):
            return {"options": {x: {"acceptable": x == "D", "reason": "r", "rule": "x y z"} for x in "ABCD"}}
        return base(prompt, **k)
    flaky.bad = lambda p: "p1" in p  # one specific generated item always fails verification
    monkeypatch.setattr(llm, "generate_json", flaky)
    st = FakeStore()
    r = session.run_session(sections=("rw",), seed=2, limit=6, output_dir=tmp_path, blueprints=[], store=st,
                            checker=OriginalityChecker(refs=[]), workers=4)
    got = r["sections"]["rw"]
    assert got["accepted"] + len(r["failures"]) == 6 and got["complete"] == (not r["failures"])


def test_quota_error_is_not_retried_and_logged(monkeypatch, caplog):
    class Quota(Exception):
        status = 429
    calls = []
    def boom(*a):
        calls.append(1)
        raise Quota("quota exceeded. Please retry in 10h56m12.6s")
    monkeypatch.setattr(llm, "PROVIDERS", {**llm.PROVIDERS, "gemini": boom, "groq": lambda *a: '{"a": 1}'})
    monkeypatch.setattr(llm, "_cooldown", {})
    assert llm.generate_json("x") == {"a": 1} and len(calls) == 1
    assert "provider gemini" in caplog.text and "Quota" in caplog.text
    llm.generate_json("x")
    assert len(calls) == 1  # cooled down


def test_status_reads_int_code_when_status_is_string():
    class E(Exception):
        status, code = "RESOURCE_EXHAUSTED", 429
    assert llm._status(E()) == 429


def test_rw_verify_is_single_call_but_math_keeps_solver(monkeypatch):
    seen = []
    base = fake_llm()
    monkeypatch.setattr(llm, "generate_json", lambda p, **k: seen.append(p) or base(p, **k))
    assert verify.verify(generator.generate(slot()))[0]
    assert sum("Evaluate EACH" in p for p in seen) == 1 and not any("Pick the best" in p or "Solve." in p for p in seen)
    seen.clear()
    verify.verify(generator.generate(slot("math")))
    assert any("Solve." in p for p in seen)


def test_progress_json_and_lines(tmp_path, capsys):
    from sat_engine.progress import Progress
    p = Progress(tmp_path / "progress.json", {"rw": 2, "math": 1}, {"rw": 1}, echo=True, heartbeat=0)
    with p:
        p.event(slot(), "OK", "nvidia", 41)
        p.event(slot(), "REJ", note="bad")
        p.beat()
    out = capsys.readouterr().out
    assert "[  2/3] rw m1 easy Words in Context  OK" in out and "nvidia 41s" in out and "rw 2/2 math 0/1" in out and "rej 1" in out and "[heartbeat]" in out
    import json
    d = json.loads((tmp_path / "progress.json").read_text())
    assert d["accepted"] == 2 and d["rejected"] == 1 and set(d["providers"]) >= {"nvidia", "gemini"}
