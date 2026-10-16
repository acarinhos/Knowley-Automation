import pytest
from pydantic import ValidationError

from sat_engine.schema import SATQuestion


def mk(**kw):
    base = dict(id="1", section="rw", module=1, difficulty="easy", domain="Craft and Structure",
                skill="Words in Context", stem="s", answer_type="mcq",
                options={"A": "a", "B": "b", "C": "c", "D": "d"}, correct_answer="B", explanation="e")
    base.update(kw)
    return SATQuestion(**base)


def test_valid_mcq():
    assert mk().correct_answer == "B"


@pytest.mark.parametrize("kw", [
    dict(options={"A": "a", "B": "a", "C": "c", "D": "d"}),
    dict(options={"A": "a", "B": "b", "C": "c"}),
    dict(correct_answer="E"),
    dict(skill="Nope"),
    dict(domain="Algebra"),
])
def test_invalid(kw):
    with pytest.raises(ValidationError):
        mk(**kw)


def test_grid_in():
    q = mk(section="math", domain="Algebra", skill="Linear functions", answer_type="grid_in",
           options=None, correct_answer="3/4")
    assert q.answer_type == "grid_in"
    for bad in ("abc", "1/", ""):
        with pytest.raises(ValidationError):
            mk(section="math", domain="Algebra", skill="Linear functions", answer_type="grid_in",
               options=None, correct_answer=bad)
