import os
from types import SimpleNamespace

import pytest

from sat_engine import __main__ as cli, llm, ui_panel as u
from sat_engine.pipeline import verify


@pytest.fixture
def keys(monkeypatch):
    for k in llm.KEY_ENV.values():
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(llm, "claude_ready", lambda: False)


def test_parse_providers(keys):
    assert llm.parse_providers("groq, gemini,groq") == (["groq", "gemini"], [])
    with pytest.raises(ValueError, match="bilinmeyen"):
        llm.parse_providers("gemini,foo")
    p, w = llm.parse_providers("gemini,claude")
    assert p == ["gemini"] and "claude" in w[0]
    with pytest.raises(ValueError):
        llm.parse_providers("claude")


def test_parse_models():
    assert llm.parse_models("gemini=g-1,groq=openai/x:y") == {"gemini": "g-1", "groq": "openai/x:y"}
    for bad in ("gemini", "foo=1", "gemini="):
        with pytest.raises(ValueError):
            llm.parse_models(bad)


def test_cli_passes_restricted_set_and_model_env_is_temporary(keys, monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_run(secs, seed, limit, **kw):
        seen.update(kw, model=llm.model_for("gemini"), claude=llm.model_for("claude"))
        return {"sections": {}, "failures": [], "ok": True, "session_id": "s", "store": "x"}

    monkeypatch.setattr(cli, "run_session", fake_run)
    monkeypatch.setattr(cli, "OUTPUT_DIR", tmp_path)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("SAT_CLAUDE_MODEL", raising=False)
    assert cli.main(["--seed", "1", "--providers", "groq,gemini", "--models", "gemini=my-model,claude=opus"]) == 0
    assert seen["providers"] == ["groq", "gemini"] and seen["model"] == "my-model" and seen["claude"] == "opus"
    assert "GEMINI_MODEL" not in os.environ and "SAT_CLAUDE_MODEL" not in os.environ
    cli.main(["--seed", "1", "--providers", "gemini"])
    assert "tek sağlayıcı" in capsys.readouterr().out
    cli.main(["--seed", "1"])
    assert seen["providers"] is None
    with pytest.raises(SystemExit):
        cli.main(["--seed", "1", "--providers", "nope"])


def test_judge_differs_from_generator_in_restricted_set():
    q = SimpleNamespace(meta=SimpleNamespace(provider="groq"))
    assert verify.judge_providers(q, ["groq", "cohere"])[0] == "cohere"
    assert verify.exclusions(q) == ["groq"]
    assert verify.judge_providers(q, ["groq"]) == ["groq"]  # single provider: unavoidable


def test_build_command_providers():
    c = u.build_command("rw", 1, providers=["groq", "gemini"], models={"groq": "m"})
    assert c[c.index("--providers") + 1] == "groq,gemini" and c[c.index("--models") + 1] == "groq=m"
    assert "--providers" not in u.build_command("rw", 1) and "--providers" not in u.build_command("rw", 1, extra=("--audit",), providers=["a"])


def test_selection_and_prefs(tmp_path):
    d = {"gemini": "g", "groq": "q"}
    assert u.selection(["groq", "gemini"], {"gemini", "groq"}, {"gemini": "g", "groq": "new"}, d) == (["groq", "gemini"], {"groq": "new"})
    with pytest.raises(ValueError):
        u.selection(["gemini"], set(), {}, d)
    f = tmp_path / "p.json"
    assert u.load_prefs(f) == {}
    u.save_prefs({"order": ["groq"]}, f)
    assert u.load_prefs(f) == {"order": ["groq"]}
    f.write_text("{bad")
    assert u.load_prefs(f) == {}
    assert u.provider_health({"providers": {"groq": {"cooldown_s": 5, "n429": 2}}}, "groq").startswith("bekleme 5s")
