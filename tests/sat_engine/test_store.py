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


class _Snap:
    def __init__(self, ref, d): self.reference, self._d = ref, d
    def to_dict(self): return {k: v for k, v in self._d.items() if k in ("section", "tur")}


class _Ref:
    def __init__(self, db, k): self.db, self.k = db, k


class _Batch:
    def __init__(self, db): self.db, self.ops = db, []
    def update(self, ref, data): self.ops.append((ref, data))
    def commit(self):
        for ref, data in self.ops:
            assert ref.k in self.db.docs  # update never creates
            self.db.docs[ref.k].update(data)
        self.db.commits += 1


class _BDB:
    def __init__(self, docs): self.docs, self.commits, self.names = docs, 0, []
    def collection(self, n): self.names.append(n); return self
    def select(self, f): assert f == ["section", "tur"]; return self
    def stream(self): return [_Snap(_Ref(self, k), v) for k, v in self.docs.items()]
    def batch(self): return _Batch(self)


def _docs():
    return {"a": {"section": "rw", "stem": "x"}, "b": {"section": "math", "tur": "eng", "stem": "y"},
            "c": {"section": "rw", "tur": "eng"}, "d": {"section": "weird"}, "e": {"stem": "z"}}


def test_backfill_dry_run_and_gate(monkeypatch):
    monkeypatch.setenv(store.WRITES_ENV, "1")
    db = _BDB(_docs())
    r = store.backfill_tur(db=db)
    assert (r["total"], r["ok"], r["to_update"], r["unknown_section"], r["updated"]) == (5, 1, 2, 2, 0) and not db.commits
    monkeypatch.delenv(store.WRITES_ENV)
    r = store.backfill_tur(dry_run=False, db=db)
    assert r["dry_run"] and r["updated"] == 0 and not db.commits and "tur" not in db.docs["a"]


def test_backfill_writes_only_tur_and_idempotent(monkeypatch):
    monkeypatch.setenv(store.WRITES_ENV, "true")
    db = _BDB(_docs())
    r = store.backfill_tur(dry_run=False, db=db)
    assert r["updated"] == 2 and not r["dry_run"]
    assert db.docs["a"] == {"section": "rw", "stem": "x", "tur": "eng"} and db.docs["b"] == {"section": "math", "tur": "math", "stem": "y"}
    assert db.docs["d"] == {"section": "weird"} and db.docs["e"] == {"stem": "z"} and set(db.docs) == set("abcde")
    r2 = store.backfill_tur(dry_run=False, db=db)
    assert r2["to_update"] == 0 and r2["updated"] == 0 and db.commits == 1
