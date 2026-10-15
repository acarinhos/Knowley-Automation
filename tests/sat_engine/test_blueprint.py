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


def test_extract_maps_and_filters(monkeypatch):
    monkeypatch.setattr(ex, "iter_pages", lambda s: [(1, PAGE), (2, PAGE)])
    items = [item(skill="words in context!"), item(skill="nonsense xyz"),
             item(stem_pattern="the committee reviewed the proposal carefully before announcing"),
             item(visual_kind="bogus")]
    bps = ex.extract_blueprints("sample4", pages=[1], llm=lambda p, system=None: {"blueprints": items})
    assert [b.id for b in bps] == ["sample4-p1-1", "sample4-p1-4"]
    assert bps[0].skill == "Words in Context" and bps[1].visual_kind is None


def test_merge_load_coverage(tmp_path):
    b = Blueprint.model_validate(item(id="x", source_tag="sample4", page=1))
    p = tmp_path / "b.json"
    ex.merge_save([b], p)
    assert ex.merge_save([b], p) == 1
    assert ex.coverage(ex.load_blueprints(p))["difficulty"] == {"easy": 1}
