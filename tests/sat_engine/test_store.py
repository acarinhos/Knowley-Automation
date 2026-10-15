import pytest

from sat_engine import store
from sat_engine.schema import SATQuestion


class Doc:
    def __init__(self, d, k): self.d, self.k = d, k
    @property
    def exists(self): return self.k in self.d
    def get(self): return self
    def set(self, v): self.d[self.k] = v


class Col:
    def __init__(self, d): self.d = d
    def document(self, k): return Doc(self.d, k)


class DB:
    def __init__(self): self.data, self.names = {}, []
    def collection(self, n): self.names.append(n); return Col(self.data)


def q(stem="What?"):
    return SATQuestion(id="1", section="rw", module=1, difficulty="easy", domain="Craft and Structure",
                       skill="Words in Context", stem=stem, answer_type="mcq",
                       options={"A": "a", "B": "b", "C": "c", "D": "d"}, correct_answer="A", explanation="e")


def test_dry_run_default(monkeypatch):
    monkeypatch.setenv(store.WRITES_ENV, "1")
    db = DB()
    r = store.save_questions([q()], db=db)
    assert r["dry_run"] and r["written"] == 0 and not db.data


def test_blocked_without_flag(monkeypatch):
    monkeypatch.delenv(store.WRITES_ENV, raising=False)
    db = DB()
    assert store.save_questions([q()], dry_run=False, db=db)["written"] == 0 and not db.data


def test_write_and_dedupe(monkeypatch):
    monkeypatch.setenv(store.WRITES_ENV, "true")
    db = DB()
    r = store.save_questions([q(), q("  what?  ")], dry_run=False, session_id="s", db=db)
    assert (r["written"], r["skipped"]) == (1, 1)
    assert db.names == ["generated_sat_questions"] and list(db.data.values())[0]["session_id"] == "s"


@pytest.mark.parametrize("name", ["questions", "other"])
def test_collection_guard(name):
    with pytest.raises(ValueError):
        store.save_questions([q()], collection=name)
