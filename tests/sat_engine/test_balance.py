import json
import re
from collections import Counter

from sat_engine import llm
from sat_engine.__main__ import main
from sat_engine.pipeline import balance, generator, planner, session, verify
from sat_engine.pipeline.originality import OriginalityChecker
from sat_engine.schema import SATQuestion
from tests.sat_engine.test_pipeline import FakeStore, fake_llm, slot


def item(i, key="A", section="rw", style="option", opts=None):
    o = opts or {a: f"text{i}{a} " + "x" * 12 for a in "ABCD"}
    if style == "option":
        ex = " ".join(f"Option {a} ({o[a][:6]}) is {'correct' if a == key else 'wrong'}." for a in "ABCD") + " Option B and D differ from A and C."
    else:  # bare-letter + 'Distractor rules: A: ...' style
        ex = (" ".join(f"{a} {'correctly' if a == key else 'incorrectly'} handles {o[a][:6]}." for a in "ABCD")
              + " Distractor rules: " + "; ".join(f"{a}: rule-{o[a][:6]}" for a in "ABCD" if a != key) + ".")
    dom, skill = ("Craft and Structure", "Words in Context") if section == "rw" else ("Algebra", "Linear equations in one variable")
    return SATQuestion(id=f"q{i}", section=section, module=1, difficulty="easy", domain=dom, skill=skill, stem=f"stem number {i}?",
                       answer_type="mcq", options=o, correct_answer=key, explanation=ex)


def consistent(q, style):
    """Every letter reference in the explanation must point at the option text it was written about."""
    if style == "option":
        pairs = re.findall(r"Option ([A-D]) \((\S+)\) is (correct|wrong)", q.explanation)
        assert len(pairs) == 4
        for a, t, verdict in pairs:
            assert q.options[a].startswith(t) and (verdict == "correct") == (a == q.correct_answer)
    else:
        found = re.findall(r"(?:^| )([A-D]) (correctly|incorrectly) handles (\S+?)\.", q.explanation)
        assert len(found) == 4
        for a, verdict, t in found:
            assert q.options[a].startswith(t) and (verdict == "correctly") == (a == q.correct_answer)
        rules = re.search(r"Distractor rules: (.*)\.$", q.explanation)[1].split("; ")
        assert [r[0] for r in rules] == sorted(a for a in "ABCD" if a != q.correct_answer)
        for r in rules:
            assert q.options[r[0]].startswith(r.split("rule-")[1])


def test_remap_no_chaining_and_styles():
    m = {"A": "B", "B": "A", "C": "D", "D": "C"}
    assert balance.remap_explanation("Option A is right; Option B is wrong. (C) and (D).", m) == "Option B is right; Option A is wrong. (D) and (C)."
    assert balance.remap_explanation("Options A and C fail, choice D works.", m) == "Options B and D fail, choice C works."
    assert balance.remap_explanation("A: parallelism; C: tense.", m) == "B: parallelism; D: tense."
    assert balance.remap_explanation("A rapid method works. B is wrong.", m) == "A rapid method works. A is wrong."  # article kept
    assert balance.remap_explanation("A correctly names it, but C does not.", m) == "B correctly names it, but D does not."
    rev = {"A": "D", "B": "C", "C": "B", "D": "A"}
    assert balance.remap_explanation("Distractor rules: A: z; B: y; D: x.", rev) == "Distractor rules: A: x; C: y; D: z."
    assert balance.remap_explanation("The answer is not B.", m) is None  # unrecognised bare letter: refuse
    assert balance.remap_explanation("In the figure B is a vertex; Option A fits.", m, context="point B on the line") == "In the figure B is a vertex; Option B fits."


def test_balance_positions_keeps_explanations_consistent_and_balanced():
    qs, styles = {}, {}
    for i in range(24):
        styles[f"r{i}"] = "option" if i % 2 else "rules"
        qs[f"r{i}"] = item(i, key="A", style=styles[f"r{i}"])
    for i in range(10):
        styles[f"m{i}"] = "option"
        qs[f"m{i}"] = item(100 + i, key="A", section="math")
    stats = balance.balance_positions(qs, seed=5)
    assert stats["fixable"] == 34 and stats["unfixable_explanation"] == 0
    for k, q in qs.items():
        consistent(q, styles[k])
    ks = balance.key_stats(qs.values())
    assert max(ks["overall"].values()) - min(ks["overall"].values()) <= 2
    for sec in ks["sections"].values():
        assert max(sec.values()) - min(sec.values()) <= 1


def test_balance_leaves_cross_referencing_and_sorted_numeric_items():
    refs = item(1, key="A", opts={"A": "alpha one", "B": "both A and B", "C": "gamma three", "D": "all of the above"})
    nums = item(2, key="B", section="math", opts={"A": "1", "B": "2", "C": "3", "D": "4"})
    nums.explanation = "Option B is correct."
    qs = {"r": refs, "n": nums}
    assert balance.shuffle_class(refs) is None and balance.shuffle_class(nums) == "reverse"
    st = balance.balance_positions(qs, seed=1)
    assert st["unfixable_reference"] == 1 and refs.correct_answer == "A" and list(refs.options.values())[1] == "both A and B"
    assert list(nums.options.values()) in (["1", "2", "3", "4"], ["4", "3", "2", "1"]) and nums.options[nums.correct_answer] == "2"
    assert nums.explanation == f"Option {nums.correct_answer} is correct."


def test_length_detection_and_verify_soft_check():
    long_key = {"A": "short one", "B": "tiny two", "C": "wee three", "D": "this is a much much longer correct option"}
    assert balance.key_is_longest(long_key, "D") and verify.key_dominates(long_key, "D")
    even = {"A": "about the same", "B": "about the same!", "C": "about the same?", "D": "about the same."}
    assert not balance.key_is_longest(even, "D") and not verify.key_dominates(even, "D")
    assert not verify.key_dominates({"A": "1", "B": "2", "C": "3", "D": "4444444444"}, "D")  # numeric options are exempt


def test_verify_rejects_dominant_key(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", fake_llm())
    q = generator.generate(slot("rw"))
    q.options = {"A": "aa one", "B": "bb two", "C": "cc three", "D": "a very long and detailed correct choice"}
    q.correct_answer = "D"
    ok, why = verify.verify(q)
    assert not ok and "longest option" in why


def _biased_session(tmp_path, n=3):
    d = {}
    for i in range(n):
        q = item(i, key="A")
        q.options = {"A": f"the long detailed correct answer{i} text here{i}", "B": f"short b{i}", "C": f"short c{i}", "D": f"short d{i}"}
        q.meta.provider = "groq"
        d[str(i)] = q.model_dump()
    p = tmp_path / "seed1-rw.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def rewriter(good=True, calls=None):
    base = fake_llm(defensible=("A",))

    def f(prompt, system=None, schema=None, providers=None, images=None, exclude=None):
        if "Rewrite ONLY" in prompt:
            if calls is not None:
                calls.append((providers, exclude))
            n = re.search(r"stem number (\d+)", prompt)[1]
            wrong = f"a longer wrong option text{n} of similar size{n}" if good else "tiny"
            return {"options": {"B": wrong + " b", "C": wrong + " c", "D": wrong + " d"}, "explanation": "Option A is right and the others are wrong."}
        return base(prompt, system, schema, providers, images, exclude)
    return f


def test_balance_session_rewrites_then_balances_and_backs_up(monkeypatch, tmp_path):
    p = _biased_session(tmp_path, 4)
    calls = []
    monkeypatch.setattr(llm, "generate_json", rewriter(True, calls))
    monkeypatch.setattr(llm, "default_order", lambda: ["groq", "gemini", "claude"])
    r = balance.balance_session("seed1-rw", tmp_path, checker=OriginalityChecker(refs=[]), progress=lambda m: None)
    assert r["length_biased"] == 4 and r["length_fixed"] == 4 and r["changed_items"] == 4
    assert calls and all(c[0] == ["groq", "gemini"] and not c[1] for c in calls)  # every non-claude provider, generator not excluded
    assert (tmp_path / "seed1-rw.json.pre_balance.bak").is_file()
    new = [SATQuestion.model_validate(x) for x in json.loads(p.read_text(encoding="utf-8")).values()]
    assert not any(balance.key_is_longest(q.options, q.correct_answer) for q in new)
    assert sorted(Counter(q.correct_answer for q in new).values()) == [1, 1, 1, 1]
    assert all(q.options[q.correct_answer].startswith("the long detailed") for q in new)
    assert r["audit"]["failed"] == 0


def test_balance_session_keeps_original_when_rewrite_fails(monkeypatch, tmp_path):
    p = _biased_session(tmp_path, 1)
    monkeypatch.setattr(llm, "generate_json", rewriter(False))
    r = balance.balance_session("seed1-rw", tmp_path, checker=OriginalityChecker(refs=[]), progress=lambda m: None)
    assert r["length_fixed"] == 0 and sum(r["length_failed"].values()) == 1
    q = SATQuestion.model_validate(next(iter(json.loads(p.read_text(encoding="utf-8")).values())))
    assert q.options["B"] == "short b0"


def test_balance_cli_dry_run_writes_nothing(tmp_path, capsys):
    p = _biased_session(tmp_path, 2)
    before = p.read_bytes()
    assert main(["--balance", "--dry-run", "--session", "seed1-rw", "--output-dir", str(tmp_path)]) == 0
    assert p.read_bytes() == before and not (tmp_path / "seed1-rw.json.pre_balance.bak").exists()
    assert "dry-run" in capsys.readouterr().out


def test_planner_assigns_balanced_target_letters_and_prompt_uses_it():
    plan = planner.build_plan(7, blueprints=[])
    for sec in ("rw", "math"):
        mcq = [s for s in plan if s.section == sec and s.blueprint.get("answer_type") != "grid_in"]
        c = Counter(s.target_key_letter for s in mcq)
        assert set(c) == set("ABCD") and max(c.values()) - min(c.values()) <= 1
    assert all(not s.target_key_letter for s in plan if s.blueprint.get("answer_type") == "grid_in")
    s = plan[0]
    pr = generator.build_prompt(s)
    assert f'correct_answer must be "{s.target_key_letter}"' in pr and "+-15%" in pr


def test_run_session_balances_new_items_only(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "generate_json", fake_llm())  # fake always keys "B"
    kw = dict(sections=("rw",), seed=3, limit=8, output_dir=tmp_path, blueprints=[], store=FakeStore(), checker=OriginalityChecker(refs=[]))
    r = session.run_session(**kw)
    q = [SATQuestion.model_validate(x) for x in json.loads(open(r["output"], encoding="utf-8").read()).values()]
    assert sorted(Counter(x.correct_answer for x in q).values()) == [2, 2, 2, 2]
    snap = open(r["output"], encoding="utf-8").read()
    session.run_session(**{**kw, "checker": OriginalityChecker(refs=[])})  # resume: nothing generated, nothing reshuffled
    assert open(r["output"], encoding="utf-8").read() == snap


def test_length_metrics_and_candidates():
    good = item(0)
    bad = item(1)
    bad.options = {"A": "the long detailed correct answer here", "B": "short b", "C": "short c", "D": "short d"}
    num = item(2, section="math")
    num.options = {"A": "1", "B": "22", "C": "333", "D": "4444"}
    qs = {"0": good, "1": bad, "2": num}
    st = balance.length_stats(qs.values())
    assert st["rw"]["mcq"] == 2 and st["rw"]["longest"] == 1 and st["rw"]["within"] == 1 and st["math"]["nonnumeric"] == 0
    assert balance.length_candidates(qs) == ["1"]  # numeric items are never rewritten
    assert balance.in_band(good.options, "A") and not balance.length_ok(bad.options, "A")


def test_rewrite_retries_with_other_provider_and_backup_not_overwritten(monkeypatch, tmp_path):
    p = _biased_session(tmp_path, 1)
    seen = []
    base = rewriter(True)

    def f(prompt, system=None, schema=None, providers=None, images=None, exclude=None):
        if "Rewrite ONLY" in prompt:
            seen.append(list(exclude))
            if len(seen) == 1:  # first attempt fails (e.g. every provider rate limited)
                raise llm.LLMError("all providers failed: gemini: 429")
            llm._last.info = {"provider": "gemini"}
        return base(prompt, system, schema, providers, images, exclude)
    monkeypatch.setattr(llm, "generate_json", f)
    monkeypatch.setattr(llm, "default_order", lambda: ["gemini", "groq"])
    (tmp_path / "seed1-rw.json.pre_balance.bak").write_text("old")
    r = balance.balance_session("seed1-rw", tmp_path, checker=OriginalityChecker(refs=[]), progress=lambda m: None)
    assert r["length_fixed"] == 1 and seen == [[], []]
    assert (tmp_path / "seed1-rw.json.pre_balance2.bak").is_file() and (tmp_path / "seed1-rw.json.pre_balance.bak").read_text() == "old"
