import itertools
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
    def f(prompt, system=None, schema=None, providers=None, images=None):
        if "Pick the best answer" in prompt:
            return {"choice": solver_choice}
        if "defensibly correct" in prompt:
            return {"defensible": list(defensible)}
        if "Solve." in prompt:
            return {"answer": "10", "choice": solver_choice}
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
    monkeypatch.setattr(llm, "generate_json", fake_llm(solver_choice="C"))
    assert not verify.verify(q)[0]
    monkeypatch.setattr(llm, "generate_json", fake_llm(defensible=("B", "D")))
    assert "defensible" in verify.verify(q)[1]
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
    monkeypatch.setattr(llm, "generate_json", fake_llm(solver_choice="D"))
    r = session.run_session(sections=("rw",), seed=1, limit=3, output_dir=tmp_path, blueprints=[],
                            store=FakeStore(), checker=OriginalityChecker(refs=[]))
    assert not r["ok"] and r["sections"]["rw"]["accepted"] == 0 and len(r["failures"]) == 1


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
    monkeypatch.setattr(llm, "generate_json", fake_llm(solver_choice="D"))
    r = session.run_session(sections=("rw",), seed=1, limit=1, output_dir=tmp_path, blueprints=[],
                            store=FakeStore(), checker=OriginalityChecker(refs=[]))
    assert r["rejections"] == {"solver disagrees with key": session.MAX_TRIES}


def test_official_stem_boilerplate_is_not_flagged():
    stem = "Which choice completes the text so that it conforms to the conventions of Standard English?"
    chk = OriginalityChecker(refs=[set(shingles(stem))])
    q = SimpleNamespace(passage="Zebras graze at dusk", options={"A": "runs", "B": "run"}, stem=stem)
    assert chk.check(q)[0]
