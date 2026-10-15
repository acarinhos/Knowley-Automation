from sat_engine.blueprint import extract as ex
from sat_engine.blueprint.guard import copies_source
from sat_engine.blueprint.model import Blueprint

PAGE = ("The committee reviewed the proposal carefully before announcing its decision to the public on Monday. " * 3)


def item(**kw):
    d = dict(section="rw", domain="Craft and Structure", skill="Words in Context", difficulty="easy",
             answer_type="mcq", stem_pattern="Pick the word that best fits the context of a sentence.",
             reasoning_steps=["infer tone", "test each option"], distractor_types=["too extreme"],
             needs_visual=False)
    d.update(kw)
    return d


def test_guard():
    assert copies_source(["reviewed the proposal carefully before announcing"], PAGE)
    assert not copies_source(["asks which word fits the context"], PAGE)


def test_extract_maps_and_filters(monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "iter_pages", lambda s: [(1, PAGE), (2, PAGE)])
    monkeypatch.setattr(ex, "_meta", lambda s: {})
    items = [item(skill="words in context!"), item(skill="nonsense xyz", stem_pattern="zzz qqq"),
             item(stem_pattern="the committee reviewed the proposal carefully before announcing"),
             item(visual_kind="bogus")]
    bps = ex.extract_blueprints("sample4", pages=[1], llm=lambda p, system=None: {"blueprints": items},
                                state=tmp_path / "s.json", out=tmp_path / "b.json")
    assert [b.id for b in bps] == ["sample4-p1-1", "sample4-p1-3"]
    assert bps[0].skill == "Words in Context" and bps[1].visual_kind is None


def test_merge_load_coverage(tmp_path):
    b = Blueprint.model_validate(item(id="x", source_tag="sample4", page=1))
    p = tmp_path / "b.json"
    ex.merge_save([b], p)
    assert ex.merge_save([b], p) == 1
    assert ex.coverage(ex.load_blueprints(p))["difficulty"] == {"easy": 1}


def test_skill_mapping_keyword_and_llm():
    assert ex.map_skill("math", "weird", "find the percent increase")[1] == "Percentages"
    assert ex.map_skill("math", "zzz", "qqq", llm=lambda p, system=None: {"skill": "Circles"})[1] == "Circles"
    assert ex.map_skill("math", "zzz", "qqq", llm=lambda p, system=None: {"skill": "none"}) is None


def test_reword_retry_and_drop_reasons():
    bad = item(stem_pattern="the committee reviewed the proposal carefully before announcing")

    def llm(p, system=None):
        if "MORE ABSTRACTLY" in p:
            return {"stem_pattern": "asks about a group's deliberation", "reasoning_steps": [], "distractor_types": []}
        return {"blueprints": [bad, item(skill="qq zz", stem_pattern="zzz qqq"), "junk", item(difficulty="bogus")]}

    bps, drops = ex.page_blueprints("sample4", 1, PAGE, llm)
    assert len(bps) == 1 and bps[0].stem_pattern.startswith("asks about")
    assert drops == {"skill_unmatched": 1, "invalid_schema": 2}

    def boom(p, system=None):
        raise RuntimeError("x")

    assert ex.page_blueprints("sample4", 1, PAGE, boom)[1] == {"llm_error": 1}
    assert ex.page_blueprints("sample4", 1, PAGE, lambda p, system=None: {"blueprints": []})[1] == {"empty": 1}


def test_chunks_resume_and_low_text(monkeypatch, tmp_path):
    math = "Solve 3x + 2 = 11 for x. Student-produced response grid. y = 2x, x = 4, a = b = c."
    monkeypatch.setattr(ex, "iter_pages", lambda s: [(1, PAGE), (2, "x"), (3, math), (4, PAGE)])
    monkeypatch.setattr(ex, "_meta", lambda s: {"2": {"low_text": True}})
    assert ex.chunk_pages([1, 2, 3, 4, 6]) == [[1, 2], [3, 4], [6]]
    calls = []

    def llm(p, system=None):
        calls.append(p)
        return {"blueprints": [item(page=1)]}

    kw = dict(llm=llm, state=tmp_path / "s.json", out=tmp_path / "b.json", workers=2)
    assert len(ex.extract_blueprints("sample4", **kw)) == 2
    assert len(calls) == 2
    assert ex.extract_blueprints("sample4", **kw) == [] and len(calls) == 2  # resumed: nothing redone
    assert ex.summary("sample4", tmp_path / "s.json")["low_text"] == 1
