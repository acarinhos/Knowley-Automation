import json
import os
import sys
import time

import pytest

from sat_engine import ui_panel as u


def test_build_command():
    c = u.build_command("rw", 5, 7, True, 2)
    assert c[:4] == [sys.executable, "-u", "-m", "sat_engine"]
    assert c[c.index("--section") + 1] == "rw" and "--save" in c and c[c.index("--limit") + 1] == "2" and c[c.index("--seed") + 1] == "5"
    assert "--save" not in u.build_command("both", 1)
    a = u.build_command("both", 9, extra=("--audit",))
    assert "--audit" in a and "--workers" not in a and a[-2:] == ["--seed", "9"]


def test_env_flag_only_when_save_and_confirmed(monkeypatch):
    monkeypatch.delenv(u.WRITE_FLAG, raising=False)
    before = dict(os.environ)
    assert u.WRITE_FLAG not in u.build_env(True, False)
    assert u.WRITE_FLAG not in u.build_env(False, True)
    assert u.build_env(True, True)[u.WRITE_FLAG] == "1"
    assert u.build_env()["PYTHONIOENCODING"] == "utf-8"
    assert dict(os.environ) == before
    assert u.WRITE_FLAG not in u.build_env(False, False, base={u.WRITE_FLAG: "1"})


def test_parse_progress():
    d = u.parse_progress({"accepted": 49, "total": 98, "sections": {"rw": {"done": 30, "target": 54}}, "eta_s": 125,
                          "providers": {"groq": {"inflight": 2, "n429": 1, "cooldown_s": 5}}})
    assert d["percent"] == 50 and "rw" not in d["sections"] and "R&W 30/54" in d["sections"]
    assert "groq" in d["providers"] and d["eta"] == "ETA ~2dk 05sn"
    assert u.parse_progress(None)["percent"] == 0


def test_is_running(tmp_path):
    prog, pid = tmp_path / "p.json", tmp_path / "pid"
    assert not u.is_running(prog, pid)
    prog.write_text(json.dumps({}))
    assert u.is_running(prog, pid)
    assert not u.is_running(prog, pid, now=time.time() + 200)
    pid.write_text(str(os.getpid()))
    assert u.is_running(prog, pid, now=time.time() + 200)


def test_panel_smoke():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    p = u.SatPanel(root)
    assert p.dry.get() and not p.save.get()
    root.destroy()
