import pytest
from pydantic import BaseModel

from sat_engine import llm


class Out(BaseModel):
    a: int


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "PROVIDERS", dict(llm.PROVIDERS))
    llm.reset_health()


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


def test_last_used_and_gemini_client_cached(monkeypatch):
    llm.PROVIDERS["groq"] = lambda *a: '{"a": 1}'
    monkeypatch.setenv("GROQ_MODEL", "mx")
    llm.generate_json("p", providers=["groq"])
    assert llm.last_used() == {"provider": "groq", "model": "mx"}
    import sys, types
    made = []
    fake = types.SimpleNamespace(Client=lambda api_key, **kw: made.append(api_key) or object())
    monkeypatch.setitem(sys.modules, "google", types.SimpleNamespace(genai=fake))
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    llm._gemini_client.cache_clear()
    assert llm._gemini_client("k") is llm._gemini_client("k") and made == ["k"]
    llm._gemini_client.cache_clear()


def test_routing_prefers_healthy_and_skips_rate_limited():
    order = []
    def mk(n, fail=None):
        def f(*a):
            order.append(n)
            if fail:
                raise Err(fail)
            return '{"a": 1}'
        return f
    llm.PROVIDERS.update(gemini=mk("gemini", 429), groq=mk("groq"), cohere=mk("cohere"), nvidia=mk("nvidia"))
    assert llm.generate_json("p") == {"a": 1}
    assert order == ["gemini", "groq"]  # 429 with a healthy alternative: no backoff wait, gemini goes into cooldown
    order.clear()
    llm.generate_json("p")
    assert order == ["groq"] and llm.stats()["gemini"]["n429"] == 1 and llm.stats()["gemini"]["cooldown_s"] > 0


def test_slow_and_failing_providers_sort_last():
    for _ in range(5):
        llm._record("groq", "timeout", 90)
        llm._record("gemini", "ok", 1)
    assert llm._penalty("groq") > llm._penalty("cohere") and llm._penalty("gemini") < llm._penalty("groq")
    seen = []
    llm.PROVIDERS.update({n: (lambda n: lambda *a: seen.append(n) or '{"a": 1}')(n) for n in llm.DEFAULT_ORDER})
    llm.generate_json("p")
    assert seen == ["gemini"]
    seen.clear()
    llm.generate_json("p", providers=["groq", "cohere"])
    assert seen == ["cohere"]
