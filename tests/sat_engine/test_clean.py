import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from sat_engine import llm, saveplan, store
from sat_engine.__main__ import main
from sat_engine.pipeline import clean, dedupe, planner, similarity
from sat_engine.pipeline.originality import OriginalityChecker
from sat_engine.schema import SATQuestion
from tests.sat_engine.test_pipeline import fake_llm


def rw(skill="Transitions", passage="x", stem="Which choice completes the text with the most logical transition?", opts=None, key="B",
       expl="Option B is right.", dom="Expression of Ideas", i=0):
    return SATQuestion(id=f"q{i}", section="rw", module=1, difficulty="easy", domain=dom, skill=skill, passage=passage, stem=stem,
                       answer_type="mcq", options=opts or {"A": "Moreover", "B": "However", "C": "Thus", "D": "Similarly"}, correct_answer=key,
                       explanation=expl)


# ---------------------------------------------------------------- Part A
def test_transition_comma_stripped_when_blank_has_comma_and_quotes_follow():
    q = rw(passage="The test failed. ______, the team rebuilt it.", opts={"A": "Moreover,", "B": "However,", "C": "Thus,", "D": "Similarly,"},
           expl="'However,' signals contrast; 'Moreover,' adds.")
    c, fx, amb = clean.clean_question(q)
    assert c.options == {"A": "Moreover", "B": "However", "C": "Thus", "D": "Similarly"}
    assert "'However' signals contrast; 'Moreover' adds." == c.explanation and not amb
    assert {k for k, _ in fx} == {"transition_comma", "explanation_quote"}


def test_transition_single_stray_comma_and_case_midsentence():
    q = rw(passage="The firm ______ issued an apology.", opts={"A": "Additionally", "B": "Consequently", "C": "Equivalently", "D": "Nevertheless,"},
           expl="'Consequently' is right; 'Nevertheless,' is a contrast.", key="B")
    c, fx, _ = clean.clean_question(q)
    assert c.options["D"] == "nevertheless" and c.options["A"] == "additionally"  # blank is mid-sentence: lowercase, no comma
    assert "'Nevertheless' is a contrast" in c.explanation  # quote case kept, comma dropped


def test_transition_sentence_start_gets_commas_on_all():
    q = rw(passage="It rained. ______ we stayed home.", opts={"A": "Moreover,", "B": "However", "C": "Thus", "D": "Similarly"})
    c, _, _ = clean.clean_question(q)
    assert all(v.endswith(",") for v in c.options.values())


def test_transition_two_two_split_midsentence_is_ambiguous():
    q = rw(passage="The firm ______ issued it.", opts={"A": "Moreover,", "B": "However,", "C": "Thus", "D": "Similarly"})
    c, fx, amb = clean.clean_question(q)
    assert [v.endswith(",") for v in c.options.values()] == [True, True, False, False] and [a[0] for a in amb] == ["transition_comma"]


def test_option_period_majority_and_tie():
    q = rw(skill="Inferences", dom="Information and Ideas", stem="Which choice most logically completes the text?", passage="p ______",
           opts={"A": "It rose.", "B": "It fell.", "C": "It held", "D": "It slid."}, expl="B is fine.")
    c, fx, _ = clean.clean_question(q)
    assert c.options["C"] == "It held." and ("option_period", "C: final period added") in fx
    t = rw(skill="Inferences", dom="Information and Ideas", stem="Which choice most logically completes the text?", passage="p ______",
           opts={"A": "It rose.", "B": "It fell.", "C": "It held", "D": "It slid"}, expl="B is fine.")
    assert [a[0] for a in clean.clean_question(t)[2]] == ["option_period"]


def test_convention_items_keep_punctuation_options():
    q = rw(skill="Boundaries", dom="Standard English Conventions", stem="Which choice completes the text so that it conforms to the conventions of Standard English?",
           passage="Cells divide ______ however, they rest.", opts={"A": ",", "B": ";", "C": ":", "D": " - "}, expl="B: a semicolon.")
    c, fx, amb = clean.clean_question(q)
    assert c.options == q.options and not fx and not amb


def test_text_artifacts():
    t, c = clean.clean_text("Solve \\(x^{2}\\)  now.\\nNext **bold** line  ", False)
    assert t == "Solve x^{2} now.\nNext bold line" and {"latex", "literal_newline", "markdown", "whitespace"} <= set(c)
    assert clean.clean_text("x \\ge 2 and \\frac{3}{5} and \\frac{p+1}{2}", True)[0] == "x ≥ 2 and 3/5 and (p+1)/2"
    assert clean.clean_text("It costs $45 million, up from $30 million.", True)[0] == "It costs $45 million, up from $30 million."  # currency kept
    assert clean.clean_text("Take $p=2$ and $0$.", True)[0] == "Take p=2 and 0."
    assert clean.clean_text("a − b and 5 – 3 and 2–3", True)[0] == "a - b and 5 - 3 and 2–3"  # spaced dash = minus, ranges stay
    assert clean.clean_text("three ways______ increased and ______is", False)[0] == "three ways ______ increased and ______ is"
    assert clean.clean_text("a − b", False)[0] == "a − b"  # rw text untouched
    assert clean.clean_text("\\neq and \\nabla", True)[0] == "≠ and \\nabla"  # \n inside a LaTeX command is not a newline


def test_leftover_latex_and_blank_marker_are_reported():
    q = rw(skill="Words in Context", dom="Craft and Structure", stem="Which choice completes the text with the most logical and precise word or phrase?",
           passage="The result was \\sqrt{2} and the ____ wide.", opts={"A": "a", "B": "b", "C": "c", "D": "d"})
    _, _, amb = clean.clean_question(q)
    assert {"latex_leftover", "blank"} <= {a[0] for a in amb}


def test_missing_blank_reported():
    q = rw(skill="Words in Context", dom="Craft and Structure", stem="Which choice completes the text with the most logical and precise word or phrase?",
           passage="There is no gap here.", opts={"A": "a", "B": "b", "C": "c", "D": "d"})
    assert ("blank", "stem asks to complete the text but the passage has no blank") in clean.clean_question(q)[2]


def test_clean_session_backup_idempotent_and_dry_run(tmp_path):
    q = rw(passage="Done. ______, we left.", opts={"A": "Moreover,", "B": "However,", "C": "Thus,", "D": "Similarly,"}, expl="'However,' wins.")
    f = tmp_path / "seed1-rw.json"
    f.write_text(json.dumps({"0": q.model_dump()}))
    r = clean.clean_session("seed1-rw", tmp_path, dry_run=True)
    assert r["items_changed"] == 1 and json.loads(f.read_text())["0"] == q.model_dump() and not (tmp_path / "seed1-rw.json.pre_clean.bak").exists()
    r = clean.clean_session("seed1-rw", tmp_path)
    assert r["items_changed"] == 1 and r["fixed_by_category"]["transition_comma"] == 1
    assert json.loads((tmp_path / "seed1-rw.json.pre_clean.bak").read_text())["0"] == q.model_dump()
    assert json.loads(f.read_text())["0"]["options"]["B"] == "However"
    assert clean.clean_session("seed1-rw", tmp_path)["items_changed"] == 0  # idempotent: no change, no new backup
    assert not (tmp_path / "seed1-rw.json.pre_clean2.bak").exists()
    f.write_text(json.dumps({"0": q.model_dump()}))
    clean.clean_session("seed1-rw", tmp_path)
    assert (tmp_path / "seed1-rw.json.pre_clean2.bak").exists()  # earlier backup never overwritten


# ---------------------------------------------------------------- Part B
SCEN_A = ("After the software update disabled the notification feature for thousands of users, the company pledged a patch within 48 hours "
          "and offered affected customers a complimentary month of premium service.")
SCEN_B = ("When the latest software update corrupted saved files for thousands of users, the development team pledged to restore the affected "
          "files within 48 hours and issued an apology to customers.")


def uniq(i):
    return {k: f"{k.lower()}word{i}" for k in "ABCD"}


def _qs(*texts):
    return {str(i): rw(passage=t, i=i, opts=uniq(i)) for i, t in enumerate(texts)}


def test_similarity_ignores_template_stems_and_finds_scenarios():
    other = "Glaciers retreat as meltwater accumulates beneath ice sheets, scientists measuring satellite elevation changes across the Greenland basin."
    qs = _qs(SCEN_A, other, SCEN_B, "Baroque composers rehearsed cantatas in candlelit chapels while patrons funded elaborate organ restorations.")
    assert similarity.content_text(qs["0"]).count("Which choice") == 0  # template stem removed
    pairs = similarity.similar_pairs(qs)
    assert [(p[1], p[2]) for p in pairs] == [("0", "2")]
    assert similarity.clusters(pairs) == [["0", "2"]]
    # identical template stems alone never make two different items similar
    assert similarity.similar_pairs(_qs(other, "Baroque composers rehearsed cantatas in candlelit chapels while patrons funded organ restorations.")) == []


def test_check_candidate_and_regen_set():
    qs = _qs(SCEN_A, "Baroque composers rehearsed cantatas in candlelit chapels while patrons funded elaborate organ restorations.")
    sim, cos, other, _ = similarity.check_candidate(rw(passage=SCEN_B, opts=uniq(7)), qs)
    assert sim and other == "0"
    assert not similarity.check_candidate(rw(passage="Mangrove roots filter brackish water and shelter juvenile fish near tidal channels every year.", opts=uniq(8)), qs)[0]
    assert dedupe.regen_set([(0.5, "1", "3", 9, []), (0.4, "3", "9", 9, []), (0.3, "1", "9", 9, [])]) == {"3": ["1"], "9": ["1"]}
    assert dedupe.regen_set([(0.5, "1", "3", 9, []), (0.4, "3", "9", 9, [])]) == {"3": ["1"]}  # 9 only resembles a regenerated item


def test_pick_topics_excludes_and_prefers_unused_vocabulary():
    qs = {"0": rw(passage="Coral reef bleaching threatens reefs; coral colonies expel algae.")}
    order = dedupe.pick_topics({"jazz improvisation"}, qs, ignore=(), used={"CRISPR gene editing"})
    assert "jazz improvisation" not in order and "CRISPR gene editing" not in order and len(order) == len(planner.TOPICS) - 2
    assert order.index("coral reef bleaching") > len(order) - 5  # words already present in the pool rank last


@pytest.fixture
def session_dir(tmp_path):
    """4 rw items at the first four plan slots (shape taken from the plan); items 0 and 2 are the same scenario."""
    plan = planner.build_plan(5, None, ("rw",))
    texts = [SCEN_A, "Glaciers retreat as meltwater accumulates beneath ice sheets, scientists measuring satellite elevation across the basin.", SCEN_B,
             "Baroque composers rehearsed cantatas in candlelit chapels while patrons funded elaborate organ restorations."]
    d = {}
    for i, t in enumerate(texts):
        s = plan[i]
        d[str(i)] = SATQuestion(id=f"seed5-rw-{i}", section="rw", module=s.module, difficulty=s.difficulty, domain=s.domain, skill=s.skill, passage=t,
                                stem="Which choice most logically completes the text?", answer_type="mcq",
                                options={"A": f"alpha{i}", "B": f"bravo{i}", "C": f"carol{i}", "D": f"delta{i}"}, correct_answer="B",
                                explanation="Option B is right.").model_dump()
    (tmp_path / "seed5-rw.json").write_text(json.dumps(d))
    return tmp_path


def _gen(passage):
    base = fake_llm()

    def f(prompt, **kw):
        d = base(prompt, **kw)
        if "Evaluate EACH" not in prompt and "Solve." not in prompt:
            d["passage"] = passage(prompt)
        return d
    return f


def test_dedupe_regenerates_later_item_with_new_topic_and_key(monkeypatch, session_dir):
    seen = []

    def passage(prompt):
        seen.append(re.search(r"Setting/topic for this item \(use it, stay original\): ([^.]*?)\.(?: |\n|$)", prompt)[1])
        return "Mangrove roots filter brackish water and shelter juvenile fish near tidal channels every year."
    monkeypatch.setattr(llm, "generate_json", _gen(passage))
    before = json.loads((session_dir / "seed5-rw.json").read_text())
    r = dedupe.dedupe_session("seed5-rw", session_dir, workers=1, progress=lambda m: None, checker_factory=lambda: OriginalityChecker(refs=[]))
    after = json.loads((session_dir / "seed5-rw.json").read_text())
    assert r["pairs"] == 1 and r["to_regenerate"] == ["2"] and list(r["regenerated"]) == ["2"] and not r["kept_original"]
    assert {i for i in after if after[i] != before[i]} == {"2"}  # only the later item changed
    assert after["2"]["correct_answer"] == "B" and after["2"]["skill"] == before["2"]["skill"] and after["2"]["difficulty"] == before["2"]["difficulty"]
    plan = planner.build_plan(5, None, ("rw",))
    assert seen[0] not in {plan[0].topic, plan[2].topic}  # topic differs from the kept item's and the old one's
    assert json.loads(Path(r["backup"]).read_text()) == before and r["pairs_after"] == 0


def test_dedupe_keeps_original_after_three_similar_attempts(monkeypatch, session_dir):
    monkeypatch.setattr(llm, "generate_json", _gen(lambda p: SCEN_A))  # candidate always repeats the kept scenario
    before = (session_dir / "seed5-rw.json").read_text()
    r = dedupe.dedupe_session("seed5-rw", session_dir, workers=1, progress=lambda m: None, checker_factory=lambda: OriginalityChecker(refs=[]))
    assert list(r["kept_original"]) == ["2"] and "too similar" in r["kept_original"]["2"] and not r["regenerated"]
    assert (session_dir / "seed5-rw.json").read_text() == before and r["backup"] is None


def test_dedupe_dry_run_makes_no_llm_call(monkeypatch, session_dir):
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: pytest.fail("LLM called in dry-run"))
    r = dedupe.dedupe_session("seed5-rw", session_dir, dry_run=True)
    assert r["to_regenerate"] == ["2"] and not r["regenerated"]


# ---------------------------------------------------------------- Part C
class FakeCol:
    def __init__(self, n):
        self.n = n

    def count(self):
        return SimpleNamespace(get=lambda timeout=None: [[SimpleNamespace(value=float(self.n))]])

    def document(self, i):
        return i

    def set(self, *a, **k):  # any write attempt fails the test
        raise AssertionError("write attempted")

    add = delete = update = set


class FakeDb:
    def __init__(self, n=0):
        self.col = FakeCol(n)

    def collection(self, name):
        assert name == "generated_sat_questions"
        return self.col

    def get_all(self, refs, timeout=None):
        return [SimpleNamespace(exists=False) for _ in refs]

    def batch(self):
        raise AssertionError("write attempted")


def _plan_dir(tmp_path, n=3):
    d = {str(i): rw(passage=f"p{i} ______ unique {i}", stem=f"Which choice most logically completes the text? {i}", i=i).model_dump() for i in range(n)}
    (tmp_path / "seed9-rw.json").write_text(json.dumps(d))
    return tmp_path


def test_save_plan_output_and_read_only(monkeypatch, tmp_path):
    import firebase_admin
    monkeypatch.setattr(saveplan, "get_db", lambda: FakeDb(0))
    monkeypatch.setattr(firebase_admin, "get_app", lambda *a, **k: SimpleNamespace(project_id="proj-x"))
    monkeypatch.delenv(store.WRITES_ENV, raising=False)
    monkeypatch.setattr(store, "save_questions", lambda *a, **k: pytest.fail("save_plan must not call save_questions"))
    out = saveplan.save_plan("seed9-rw", _plan_dir(tmp_path))
    assert "collection: generated_sat_questions" in out and "documents to write: 3 (unique ids 3" in out
    assert "max " in out and "of the 1 MiB limit" in out and f"{store.WRITES_ENV} absent" in out
    assert "project_id=proj-x" in out and "existing docs in generated_sat_questions: 0 (collection is empty" in out
    assert '"correct_answer": "str"' in out and '"meta": {' in out and "already present: 0/3" in out


def test_save_plan_cli_never_writes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(saveplan, "save_plan", lambda sid, od: "plan ok")
    monkeypatch.setattr(store, "save_questions", lambda *a, **k: pytest.fail("write path used"))
    assert main(["--save-plan", "--session", "seed9-rw", "--output-dir", str(_plan_dir(tmp_path))]) == 0
    assert "plan ok" in capsys.readouterr().out


def test_no_write_without_save_flag_and_env(monkeypatch):
    qs = [rw()]
    monkeypatch.setattr(store, "get_db", lambda: pytest.fail("db opened"))
    monkeypatch.delenv(store.WRITES_ENV, raising=False)
    assert store.save_questions(qs, dry_run=True)["dry_run"]  # no --save
    assert store.save_questions(qs, dry_run=False)["dry_run"]  # --save but env flag absent
    monkeypatch.setenv(store.WRITES_ENV, "0")
    assert store.save_questions(qs, dry_run=False)["dry_run"]  # flag present but falsy
    monkeypatch.setenv(store.WRITES_ENV, "1")
    assert store.save_questions(qs, dry_run=True)["dry_run"]  # flag set but no --save


def test_only_store_module_contains_firestore_writes():
    root = Path(store.__file__).parent
    pat = re.compile(r"\.(set|add|delete|create|commit|batch|bulk_writer|transaction)\(|\.update\(\{")
    for p in root.rglob("*.py"):
        if p.name != "store.py" and "firebase" in p.read_text(encoding="utf-8").lower() and p.name not in ("saveplan.py", "ui_panel.py"):  # ui_panel: Tk widgets only, spawns a subprocess
            assert not pat.search(p.read_text(encoding="utf-8")), p
    sp = (root / "saveplan.py").read_text(encoding="utf-8")
    assert not pat.search(sp.replace("ids)", "")), "saveplan must stay read-only"
    assert len(re.findall(r"\bref\.set\(", (root / "store.py").read_text(encoding="utf-8"))) == 1
    # allow-list: the one guarded backfill write (update of {"tur": ...} only; gated by --save + WRITES_ENV in backfill_tur)
    assert len(re.findall(r'batch\.update\(ref, \{"tur": want\}\)', (root / "store.py").read_text(encoding="utf-8"))) == 1


def test_build_doc_matches_written_shape():
    q = rw()
    d = store.build_doc(q, "s1")
    assert d == {**q.model_dump(), "session_id": "s1"}
