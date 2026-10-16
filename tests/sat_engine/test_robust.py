import json
import subprocess
from types import SimpleNamespace

import pytest

from sat_engine import llm
from sat_engine.__main__ import main
from sat_engine.pipeline import generator, importer, planner, session, verify
from sat_engine.pipeline.originality import OriginalityChecker, build_stoplist, shingles
from sat_engine.schema import SATQuestion


def Q(passage=None, stem="x", options=None):
    return SimpleNamespace(passage=passage, stem=stem, options=options or {"A": "a"})


# ---- originality stoplist
def test_boilerplate_grams_across_pages_are_ignored_but_content_is_strict():
    formula = "the student wants to emphasize the"
    refs = [set(shingles(f"{formula} topic{i} alpha{i} beta{i} gamma{i} delta{i}")) for i in range(3)]
    refs.append(set(shingles("rare orchid pollination depends on one specific moth species alone")))
    chk = OriginalityChecker(refs=refs)
    assert shingles(formula)[0] in chk.stop
    assert chk.check(Q("Fresh text unrelated. " + formula + " quantum lattice vibration"))[0]
    assert not chk.check(Q("rare orchid pollination depends on one specific moth species alone"))[0]
    ex = chk.explain(Q("rare orchid pollination depends on one specific moth"))
    assert "run=" in ex and "orchid" not in ex


def test_formula_counts_only_accepted_questions_and_only_in_session_comparison():
    chk = OriginalityChecker(refs=[])
    f = "after reading both authors the second writer would likely agree"
    for i in range(4):  # rejected/never-accepted candidates must not turn the phrase into a "formula"
        assert chk.check(Q(f + f" thing{i} one{i} two{i} three{i}"))[0]
    first = Q(f + " thing0 one0 two0 three0")
    chk.add(first)
    assert not chk.check(Q(f + " thing1 one1 two1 three1"))[0]  # 1 accepted use: still a copy
    chk.add(Q(f + " thing1 one1 two1 three1"))
    assert chk.check(Q(f + " novel tail words appear here"))[0]  # used by 2 accepted questions: a formula
    own = "unique volcanic basalt column cooling fractures hexagonal pattern"
    chk.add(Q(own))
    assert not chk.check(Q(own))[0]
    # the formula rule never excuses overlap with the reference pages
    ref = "ancient river deltas shaped early farming settlements across distant valleys"
    chk2 = OriginalityChecker(refs=[set(shingles(ref))])
    for i in range(3):
        chk2.add(Q(ref + f" extra{i} words{i}"))
    assert not chk2.check(Q(ref))[0]


def test_same_passage_on_three_reference_pages_is_still_rejected():
    passage = " ".join(f"w{i}x" for i in range(25))  # 25 distinct words, verbatim on 3 pages (test + key + other pdf)
    refs = [set(shingles(passage + f" tail{i} more{i} filler{i} words{i}")) for i in range(3)]
    chk = OriginalityChecker(refs=refs)
    assert shingles(passage)[0] in chk.freq  # static rule alone would have stoplisted it
    ok, score = chk.check(Q(passage))
    assert not ok and score < 1
    # near-identical pages are deduplicated before counting
    twin = [set(shingles(passage))] * 3
    assert not build_stoplist(twin) - build_stoplist([])


def test_template_grams_are_in_stoplist():
    assert shingles("Which choice most logically completes the text")[0] in build_stoplist([])


# ---- planner
def test_topics_distinct_and_plan_unchanged():
    assert len(set(planner.TOPICS)) == len(planner.TOPICS) >= 60
    p = planner.build_plan(7, blueprints=[])
    for sec in ("rw", "math"):
        t = [s.topic for s in p if s.section == sec]
        assert all(t) and len(set(t)) == len(t)
    assert all(len(s.numbers) == 3 for s in p)


def _slot():
    return planner.Slot(0, "math", 1, "Algebra", "Linear functions", "easy", True, {"id": "b"}, "mangrove restoration", (4, 9, 33))


def test_prompt_carries_topic_numbers_and_retry_changes_topic(monkeypatch):
    seen = []

    def stop(p, **k):
        seen.append(p)
        raise RuntimeError("stop")
    monkeypatch.setattr(llm, "generate_json", stop)
    pr = generator.build_prompt(_slot())
    assert "mangrove restoration" in pr and "4, 9, 33" in pr and "at least 3" in pr and "absolute" in pr
    with pytest.raises(RuntimeError):
        generator.generate(_slot(), feedback="originality: too similar to existing text")
    assert "completely different topic" in seen[0]


# ---- figures
def test_aliases_and_ranges_are_repaired():
    from sat_engine.visuals import parse_spec
    fg = {"kind": "function_graph", "curves": [{"type": "absolute_value", "a": 1}, {"type": "abs", "a": 2}],
          "points": [{"x": 0, "y": 13}, {"x": -15, "y": 0}]}
    n = generator.normalize_spec(fg)
    assert [c["type"] for c in n["curves"]] == ["absolute", "absolute"]
    assert n["y_range"][1] >= 13 and n["x_range"][0] <= -15 and "y_range" not in fg
    cl_in = {"kind": "coordinate_lines", "lines": [{"m": 1, "b": 0}, {"m": -1, "b": 40}]}
    cl = generator.normalize_spec(cl_in)
    assert cl["x_range"][1] >= 20 and cl["y_range"][1] >= 20
    ln = generator.normalize_spec({"kind": "function_graph", "show_intercepts": True, "curves": [{"type": "linear", "m": 1, "b": -30}]})
    assert ln["x_range"][1] >= 30 and ln["y_range"][0] <= -30
    for spec in (fg, cl_in, ln):
        parse_spec(generator.normalize_spec(spec))


def test_coerce_unwraps_list_string_and_spec():
    spec = {"kind": "dot_plot", "values": [1, 2, 2]}
    assert generator.coerce_spec([spec]) == generator.coerce_spec(json.dumps({"spec": spec})) == spec
    with pytest.raises(ValueError):
        generator.coerce_spec([1, 2])


def test_figure_values_optional_and_facts_in_error():
    spec = {"kind": "geometry", "shape": "right_triangle", "a": 3, "b": 4, "hypotenuse": 5, "unknown": ["c"]}
    generator.check_facts(None, spec)
    generator.check_facts({}, spec)
    generator.check_facts({"h": 5}, spec)
    with pytest.raises(ValueError, match="computed facts are:.*omit figure_values"):
        generator.check_facts({"h": 99}, spec)


# ---- llm
def test_nvidia_limits_and_env_override(monkeypatch):
    assert llm._timeout("nvidia") == 150 and llm._timeout("claude") == 180 and llm._timeout("groq") == llm.TIMEOUT
    assert llm.CONCURRENCY["nvidia"] == 4 and llm.CONCURRENCY["claude"] == 3
    monkeypatch.setenv("NVIDIA_TIMEOUT", "42")
    assert llm._timeout("nvidia") == 42


class _Proc:
    log = []

    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.pid, self.returncode, self.stdin_text = cmd, kw, 4242, 0, None
        self.hang = kw.pop("hang", False)
        _Proc.log.append(self)

    def communicate(self, input=None, timeout=None):
        if self.hang and timeout:
            self.hang = False
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        self.stdin_text = input
        return '{"a": 1}', ""

    def kill(self):
        _Proc.log.append("kill")


def test_claude_provider_runs_cli_minimal_env_and_kills_tree(monkeypatch, tmp_path):
    _Proc.log = []
    monkeypatch.setattr(subprocess, "Popen", _Proc)
    killed = []
    monkeypatch.setattr(subprocess, "run", lambda c, **k: killed.append(c) or subprocess.CompletedProcess(c, 0, stdout="", stderr=""))
    monkeypatch.setattr(llm, "_claude_flags", lambda exe: ("--strict-mcp-config", "--setting-sources"))
    monkeypatch.setenv("SAT_CLAUDE_CMD", "claude-x")
    monkeypatch.setenv("SAT_CLAUDE_MODEL", "opus")
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setenv("PATH", "somepath")
    assert llm._call_claude("hi", "sys", None, True) == '{"a": 1}'
    p = _Proc.log[0]
    cmd, kw = p.cmd, p.kw
    assert cmd[:2] == ["claude-x", "-p"] and cmd[cmd.index("--model") + 1] == "opus" and cmd[cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--setting-sources") + 1] == ""
    assert p.stdin_text == "hi" and "GEMINI_API_KEY" not in kw["env"] and kw["env"]["PATH"] == "somepath"
    assert str(kw["cwd"]) != str(tmp_path) and "sat_engine" not in str(kw["cwd"])
    # timeout -> whole process tree is killed (taskkill /T /F on Windows) and a retryable timeout error is raised
    _Proc.log = []
    monkeypatch.setattr(subprocess, "Popen", lambda c, **k: _Proc(c, hang=True, **k))
    with pytest.raises(TimeoutError, match="timed out"):
        llm._call_claude("hi", None, None, False)
    assert "kill" in _Proc.log and (not killed or killed[-1][:2] == ["taskkill", "/PID"])
    monkeypatch.setattr(subprocess, "Popen", lambda c, **k: setattr(_p := _Proc(c, **k), "returncode", 1) or _p)
    with pytest.raises(llm.LLMError, match="exit 1"):
        llm._call_claude("hi", None, None, False)
    monkeypatch.setattr(llm, "claude_ready", lambda: False)
    assert "claude" not in llm.default_order()
    monkeypatch.setattr(llm, "claude_ready", lambda: True)
    assert llm.default_order()[0] == "claude"
    assert verify.judge_providers(SimpleNamespace(meta=SimpleNamespace(provider="claude")))[0] != "claude"
    assert verify.judge_providers(SimpleNamespace(meta=SimpleNamespace(provider="groq")))[0] == "claude"
    ext = SimpleNamespace(meta=SimpleNamespace(provider="external"))
    assert verify.judge_providers(ext)[0] != "claude" and verify.judge_providers(ext)[-1] == "claude" and verify.exclusions(ext) == []


def test_judge_is_hard_excluded_from_generator_provider(monkeypatch, caplog):
    seen = []
    monkeypatch.setattr(llm, "PROVIDERS", {n: (lambda n: lambda *a: seen.append(n) or '{"ok": 1}')(n) for n in ("gemini", "groq")})
    llm.reset_health()
    for _ in range(4):
        assert llm.generate_json("p", providers=["gemini", "groq"], exclude=["gemini"]) == {"ok": 1}
    assert set(seen) == {"groq"}
    # groq cooling: the judge fails rather than falling back to the generator's provider
    llm._cooldown["groq"] = llm.time.monotonic() + 3000
    with pytest.raises(llm.LLMError):
        llm.generate_json("p", providers=["gemini", "groq"], exclude=["gemini"])
    llm.reset_health()
    seen.clear()
    with caplog.at_level("WARNING", logger="sat_engine.llm"):  # nothing else configured: same provider, logged
        llm.generate_json("p", providers=["gemini"], exclude=["gemini"])
    assert seen == ["gemini"] and "judge == generator" in caplog.text


# ---- session reasons, import, open slots
def good(skill="Cross-Text Connections", domain="Craft and Structure", diff="easy", n=0, **kw):
    return {"section": "rw", "module": 1, "difficulty": diff, "domain": domain, "skill": skill,
            "passage": f"alpha{n} beta{n} gamma{n} delta{n} epsilon{n} zeta{n} eta{n}", "stem": f"stem{n} one two three four five?",
            "answer_type": "mcq", "options": {"A": f"a{n}", "B": f"b{n}", "C": f"c{n}", "D": f"d{n}"}, "correct_answer": "B",
            "explanation": "e", **kw}


def test_validation_error_reason_names_field():
    with pytest.raises(Exception) as ei:
        SATQuestion.model_validate({**good(), "id": "x", "options": {"A": "a"}})
    assert session._why(ei.value).startswith("schema SATQuestion: ")


def test_import_places_rejects_and_open_list(monkeypatch, tmp_path):
    monkeypatch.setattr(importer.verify, "verify", lambda q, providers=None: (q.stem.startswith("stem9") is False, "solver disagrees with key"))
    items = [good(n=1), good(n=2), good(n=9), good(skill="Words in Context", n=3), {"section": "rw", "bad": 1}, good(n=1),
             good(skill="Transitions", domain="Expression of Ideas", n=5, diff="hard")]
    f = tmp_path / "in.json"
    f.write_text(json.dumps(items), encoding="utf-8")
    sid = "seed7-rw"
    before = session.open_slots(sid, tmp_path, blueprints=[])
    assert len(before) == 54
    r = importer.import_questions(f, sid, output_dir=tmp_path, blueprints=[], checker=OriginalityChecker(refs=[]))
    reasons = {x["item"]: x["reason"] for x in r["rejected"]}
    assert reasons[2] == "solver disagrees with key" and "schema" in reasons[4] and "originality" in reasons[5]
    assert r["accepted"] == len(r["placed"]) >= 3
    stored = json.loads((tmp_path / f"{sid}.json").read_text(encoding="utf-8"))
    assert len(stored) == r["accepted"] and all(SATQuestion.model_validate(v) and v["id"] == f"{sid}-{k}" for k, v in stored.items())
    after = session.open_slots(sid, tmp_path, blueprints=[])
    assert len(after) == 54 - r["accepted"] == r["open_left"]
    by = {s["index"]: s for s in before}
    assert all(by[int(k)]["skill"] == v["skill"] for k, v in stored.items())


def test_session_adopts_imported_slots_and_cli_list_open(monkeypatch, tmp_path, capsys):
    import sat_engine.__main__ as m
    sid = "seed3-rw"
    s0 = planner.build_plan(3, [], ("rw",))[0]
    q = good(skill=s0.skill, domain=s0.domain, diff=s0.difficulty, n=77)
    (tmp_path / f"{sid}.json").write_text(json.dumps({"0": {**q, "id": f"{sid}-0", "module": s0.module}}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: calls.append(1) or {})
    r = session.run_session(sections=("rw",), seed=3, limit=1, output_dir=tmp_path, blueprints=[],
                            store=SimpleNamespace(save_questions=lambda *a, **k: {}), checker=OriginalityChecker(refs=[]))
    assert not calls and r["ok"]
    monkeypatch.setattr(m, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(m, "open_slots", lambda s: session.open_slots(s, tmp_path, []))
    assert main(["--seed", "3", "--section", "rw", "--list-open"]) == 0
    assert "open slots" in capsys.readouterr().out and json.loads((tmp_path / "open_slots.json").read_text())[0]["index"] == 1
