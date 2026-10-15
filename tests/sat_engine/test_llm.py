import pytest
from pydantic import BaseModel

from sat_engine import llm


class Out(BaseModel):
    a: int


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "PROVIDERS", dict(llm.PROVIDERS))


class Err(Exception):
    def __init__(self, status):
        self.status_code = status


def test_json_repair_and_schema(monkeypatch):
    llm.PROVIDERS["groq"] = lambda *a: 'Sure:\n```json\n{"a": 1,}\n```'
    assert llm.generate_json("p", schema=Out, providers=["groq"]) == {"a": 1}


def test_retry_on_429(monkeypatch):
    calls = []

    def f(*a):
        calls.append(1)
        if len(calls) < 3:
            raise Err(429)
        return '{"a": 2}'
    llm.PROVIDERS["groq"] = f
    assert llm.generate_json("p", providers=["groq"]) == {"a": 2} and len(calls) == 3


def test_fallback_on_bad_json_and_error():
    def boom(*a): raise Err(400)
    llm.PROVIDERS["gemini"] = boom
    llm.PROVIDERS["groq"] = lambda *a: "not json"
    llm.PROVIDERS["cohere"] = lambda *a: '{"a": "x"}'  # fails schema
    llm.PROVIDERS["nvidia"] = lambda *a: '{"a": 5}'
    assert llm.generate_json("p", schema=Out) == {"a": 5}


def test_all_fail_and_text_images():
    llm.PROVIDERS["gemini"] = lambda *a: (_ for _ in ()).throw(Err(500))
    with pytest.raises(llm.LLMError):
        llm.generate_text("p", providers=["gemini"])
    seen = {}
    llm.PROVIDERS["gemini"] = lambda p, s, im, j: seen.update(im=im) or " hi "
    assert llm.generate_text("p", providers=["gemini"], images=[b"x"]) == "hi" and seen["im"] == [b"x"]
