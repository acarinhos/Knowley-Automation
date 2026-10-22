import json
import os
import sys
import time

import pytest

from sat_engine import ui_panel as u


def test_build_command():
    c = u.build_command("rw", 5, 7, True, 2)
    assert c[:4] == [u.project_python(), "-u", "-m", "sat_engine"]
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


# ---- review fixes ----

import re
import subprocess
import textwrap
import types
from pathlib import Path
from unittest import mock


def _snippets():
    md = (Path(u.__file__).parent / "UI_INTEGRATION.md").read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", md, re.S)


def test_hook_import_guard_swallows_any_error(monkeypatch):
    imp, _ = _snippets()
    if True:
        monkeypatch.setitem(sys.modules, "sat_engine.ui_panel", None)  # ImportError path
        ns = {}
        exec(imp, ns)
        assert ns["install_sat_panel"] is None
    # import-time SyntaxError
    monkeypatch.delitem(sys.modules, "sat_engine.ui_panel")
    with mock.patch("builtins.__import__", side_effect=SyntaxError("bad")):
        ns = {}
        exec(imp, ns)
    assert ns["install_sat_panel"] is None


def test_hook_call_guard_swallows_tclerror():
    import logging
    tk = pytest.importorskip("tkinter")
    _, call = _snippets()
    ns = {"logging": logging, "install_sat_panel": mock.Mock(side_effect=tk.TclError("no display")), "self": object()}
    exec(textwrap.dedent(call), ns)
    ns["install_sat_panel"].assert_called_once()
    assert "SAT panel could not be installed" in call
    # hook is applied manually; check the real source only when present
    src = (Path(u.ROOT) / "ui_tkinter.py").read_text(encoding="utf-8")
    if "install_sat_panel" not in src:
        pytest.skip("hook not applied in this ui_tkinter.py (manual integration)")
    assert "except Exception:\n    install_sat_panel = None" in src and "SAT panel could not be installed" in src


def test_parse_args_clamp_and_validation():
    assert u.parse_args("Math", "", "999", "")[:3] == ("math", None, 32)
    assert u.parse_args("Math", "5", "0", "3") == ("math", 5, 1, 3)
    assert u.parse_args("Math", "", "", "")[2] == 10
    for bad in (("", "10", "0"), ("", "10", "-2"), ("x", "10", ""), ("-1", "10", ""), ("", "abc", ""), ("", "10", "1.5")):
        with pytest.raises(ValueError):
            u.parse_args("Math", *bad)


def test_pid_alive_exact_match():
    out = '"python.exe","1234","Console","1","10 K"\n"x.exe","12345","Console","1","1 K"\n'
    with mock.patch.object(u.os, "name", "nt"), mock.patch.object(u.subprocess, "run", return_value=mock.Mock(stdout=out)):
        assert u._pid_alive(1234) and u._pid_alive(12345)
        assert not u._pid_alive(123) and not u._pid_alive(234)
    with mock.patch.object(u.os, "name", "nt"), mock.patch.object(u.subprocess, "run", return_value=mock.Mock(stdout="INFO: No tasks...")):
        assert not u._pid_alive(1234)


def test_is_running_pid_wins_over_fresh_progress(tmp_path):
    prog, pid = tmp_path / "p.json", tmp_path / "pid"
    prog.write_text("{}")
    pid.write_text("4242")
    with mock.patch.object(u, "_pid_alive", return_value=False):
        assert not u.is_running(prog, pid)  # exited, progress still fresh
    with mock.patch.object(u, "_pid_alive", return_value=True):
        assert u.is_running(prog, pid, now=time.time() + 500)  # alive, progress stale
    pid.write_text("junk")
    assert u.is_running(prog, pid)  # unreadable pid file -> freshness fallback


def test_tail_text_reads_only_end(tmp_path):
    f = tmp_path / "c.log"
    f.write_bytes(b"A" * 100000 + b"END")
    t = u.tail_text(f, 16384)
    assert len(t) == 16384 and t.endswith("END")
    assert u.tail_text(tmp_path / "missing") == ""


@pytest.fixture
def panel(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    for n in ("OUT",):
        monkeypatch.setattr(u, n, tmp_path)
    monkeypatch.setattr(u, "PREFS", tmp_path / "ui_prefs.json")
    monkeypatch.setattr(u.llm, "claude_ready", lambda: False)
    monkeypatch.setattr(u, "CONSOLE", tmp_path / "console.log")
    monkeypatch.setattr(u, "CONSOLE_ERR", tmp_path / "console.err")
    monkeypatch.setattr(u, "PIDFILE", tmp_path / "ui_run.pid")
    monkeypatch.setattr(u.SatPanel, "_probe", lambda self: None)
    p = u.SatPanel(root)
    yield p
    try:
        p.destroy()
    finally:
        root.destroy()


def _popen():
    return mock.Mock(pid=999, poll=mock.Mock(return_value=None))


def test_start_save_declined_no_spawn(panel):
    panel.dry.set(False)
    panel.save.set(True)
    with mock.patch.object(u.messagebox, "askyesno", return_value=False), mock.patch.object(u.subprocess, "Popen") as po:
        panel.start()
    po.assert_not_called()


def test_start_save_accepted_flag_only_in_child_env(panel, monkeypatch):
    monkeypatch.delenv(u.WRITE_FLAG, raising=False)
    panel.dry.set(False)
    panel.save.set(True)
    panel.limit.set("1")
    with mock.patch.object(u.messagebox, "askyesno", return_value=True), mock.patch.object(u.subprocess, "Popen", return_value=_popen()) as po:
        panel.start()
    assert "--save" in po.call_args.args[0] and po.call_args.kwargs["env"][u.WRITE_FLAG] == "1"
    assert u.WRITE_FLAG not in os.environ


def test_start_dry_run_never_saves(panel):
    panel.dry.set(True)
    panel.save.set(True)  # contradictory state: dry wins
    with mock.patch.object(u.messagebox, "askyesno") as ask, mock.patch.object(u.subprocess, "Popen", return_value=_popen()) as po:
        panel.start()
    ask.assert_not_called()
    assert "--save" not in po.call_args.args[0] and u.WRITE_FLAG not in po.call_args.kwargs["env"]


def test_spawn_does_not_truncate_held_console(panel, tmp_path):
    (tmp_path / "console.log").write_text("old")
    real = open

    def fake_open(path, mode="r", *a, **k):
        if Path(path) == tmp_path / "console.log" and "w" in mode:
            raise PermissionError
        return real(path, mode, *a, **k)

    with mock.patch("builtins.open", fake_open), mock.patch.object(u.subprocess, "Popen", return_value=_popen()):
        panel._spawn(["x"], {})
    assert (tmp_path / "console.log").read_text() == "old"
    assert panel.console != u.CONSOLE and panel.console.name.startswith("console_")


def test_orphan_adopted_and_stopped_by_pid(panel):
    panel._ext_pid, panel._ext = 5555, True
    with mock.patch.object(u.messagebox, "showwarning"):
        assert panel._busy()
    with mock.patch.object(u.messagebox, "askyesno", return_value=True), mock.patch.object(u, "kill_tree") as kt:
        panel.stop()
    kt.assert_called_once_with(5555)
    with mock.patch.object(u.os, "name", "nt"), mock.patch.object(u.subprocess, "run") as run:
        u.kill_tree(5555)
    assert run.call_args.args[0] == ["taskkill", "/PID", "5555", "/T", "/F"]


def test_tick_cancelled_on_destroy_and_no_reschedule(panel):
    panel.after_cancel = mock.Mock(wraps=panel.after_cancel)
    job = panel._job
    assert job
    panel.destroy()
    panel.after_cancel.assert_called_with(job)
    assert panel._job is None
    panel._tick()  # widget gone: must not raise nor reschedule
    assert panel._job is None


def test_panel_providers_flow(panel):
    panel.avail = ["gemini", "groq"]
    panel._render_prov()
    panel._move("groq", -1)
    panel.models["groq"].set("m-x")
    sel = panel._providers()
    assert sel == (["groq", "gemini"], {"groq": "m-x"}) and u.load_prefs()["order"][:2] == ["groq", "gemini"]
    panel._reset_prov()
    assert panel.models["groq"].get() == panel.defaults["groq"] and panel._shown()[0] == "gemini"
    for p in panel.enabled.values():
        p.set(False)
    with mock.patch.object(u.messagebox, "showerror") as se:
        assert panel._providers() is None
    se.assert_called_once()


def test_global_llm_defaults_follow_core_settings(monkeypatch):
    from core import llm_settings as g
    from sat_engine import ui_panel
    monkeypatch.setattr(g, "load_selection", lambda *a, **k: ["groq", "gemini"])
    monkeypatch.setattr(g, "load_models", lambda *a, **k: {"groq": ["x-model"], "gemini": g.available_models("gemini")})
    sel, models = ui_panel.global_llm_defaults()
    assert sel == ["groq", "gemini"] and models == {"groq": "x-model"} and "claude" not in sel


def test_project_python_selection(tmp_path):
    assert u.project_python(tmp_path, {}) == sys.executable
    v = tmp_path / "venv" / "Scripts"
    v.mkdir(parents=True)
    (v / "python.exe").write_text("")
    assert u.project_python(tmp_path, {}) == str(v / "python.exe")
    d = tmp_path / ".venv" / "Scripts"
    d.mkdir(parents=True)
    (d / "python.exe").write_text("")
    assert u.project_python(tmp_path, {}) == str(d / "python.exe")  # .venv wins over venv
    assert u.project_python(tmp_path, {"SAT_PYTHON": "X:/py.exe"}) == "X:/py.exe"


def test_build_command_uses_project_python(monkeypatch):
    monkeypatch.setattr(u, "project_python", lambda *a: "VENV_PY")
    assert u.build_command("rw", 1)[0] == "VENV_PY"
    assert u.build_command("rw", 1, extra=("--audit",))[0] == "VENV_PY"


def test_missing_deps_and_warning(monkeypatch):
    class R:
        def __init__(self, rc): self.returncode = rc
    def fake(cmd, **k):
        assert cmd[0] == "PY"
        if "firebase_admin" in cmd[-1]:
            raise OSError
        return R(1 if "matplotlib" in cmd[-1] else 0)
    monkeypatch.setattr(u.subprocess, "run", fake)
    m = u.missing_deps("PY")
    assert m == ["matplotlib", "firebase_admin"]
    w = u.deps_warning(m)
    assert w.startswith("Eksik paket: matplotlib, firebase_admin") and "run_ui.bat" in w
    assert u.deps_warning([]) == ""


def test_report_does_not_need_matplotlib(monkeypatch):
    import importlib
    monkeypatch.setitem(sys.modules, "matplotlib", None)  # import matplotlib -> ImportError
    importlib.reload(importlib.import_module("sat_engine.report"))
