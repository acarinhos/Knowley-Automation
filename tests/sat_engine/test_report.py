import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sat_engine import report, ui_panel
from sat_engine.__main__ import main

NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)  # 15:00 Istanbul


def d(created=None, key="A", section="rw", diff="easy", at="mcq", stem="s", **kw):
    doc = {"section": section, "difficulty": diff, "answer_type": at, "correct_answer": key, "stem": stem, "domain": "D", "skill": "K",
           "module": 1, "session_id": "s1", "meta": {"provider": "groq", "model": "m", **({"created_at": created} if created else {})}}
    return {**doc, **kw}


def test_empty():
    r = report.build_report([], NOW)
    assert r["total"] == 0 and r["counts"]["today"] == 0 and r["latest"] == [] and r["key_letters"] == {} and len(r["per_day_30"]) == 30
    assert "Toplam 0" in report.format_text(r)


def test_istanbul_midnight_boundary():
    # 23:30 UTC on May 9 = 02:30 May 10 Istanbul -> today; 20:59 UTC May 9 = 23:59 May 9 Istanbul -> yesterday
    r = report.build_report([d("2026-05-09T23:30:00+00:00"), d("2026-05-09T20:59:00+00:00")], NOW, tz="Europe/Istanbul")
    if "note" in r:  # tzdata missing -> fixed UTC+3 gives the same answer
        assert "UTC+3" in r["note"]
    assert r["counts"]["today"] == 1 and r["counts"]["last_7_days"] == 2
    assert r["per_day_30"]["2026-05-10"] == 1 and r["per_day_30"]["2026-05-09"] == 1


def test_week_month_windows():
    docs = [d("2026-05-04T10:00:00+00:00"), d("2026-05-03T10:00:00+00:00"),  # 6 / 7 days back
            d("2026-04-11T10:00:00+00:00"), d("2026-04-10T10:00:00+00:00")]  # 29 / 30 days back
    c = report.build_report(docs, NOW)["counts"]
    assert (c["today"], c["last_7_days"], c["last_30_days"], c["all_time"]) == (0, 1, 3, 4)


def test_unknown_bucket_and_top_level_precedence():
    top = d("2026-01-01T00:00:00+00:00", created_at=datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc))
    r = report.build_report([d(), top, d("garbage")], NOW)
    assert r["counts"]["unknown_date"] == 2 and r["counts"]["today"] == 1 and r["total"] == 3 and r["counts"]["all_time"] == 3


def test_breakdowns_and_keys():
    docs = [d(key="A"), d(key="A", section="math", diff="hard", visual={"kind": "svg"}), d(key="C"),
            d(key="12", at="grid_in", section="math"), d(key="d")]
    r = report.build_report(docs, NOW)
    assert r["by_section"] == {"rw": 3, "math": 2} and r["by_difficulty"] == {"easy": 4, "hard": 1}
    assert r["by_answer_type"] == {"mcq": 4, "grid_in": 1} and r["by_has_visual"]["visual var"] == 1
    assert r["key_letters"] == {"A": 2, "B": 0, "C": 1, "D": 1}
    assert r["by_provider"] == {"groq": 5} and r["by_session"] == {"s1": 5} and r["by_module"] == {"1": 5}
    assert r["by_tur"] == {} and r["tur_missing"] == 5 and "tur eksik: 5" in report.format_text(r)
    r3 = report.build_report([d(tur="eng"), d(section="math", tur="math"), d(tur="eng"), d()], NOW)
    assert r3["by_tur"] == {"eng": 2, "math": 1} and r3["tur_missing"] == 1
    assert "active_passive" not in r and "by_status" not in r and "by_verified" not in r
    r2 = report.build_report([d(active=True, status="approved"), d(active=False)], NOW)
    assert r2["active_passive"] == {"aktif": 1, "pasif": 1} and r2["by_status"] == {"approved": 1}


def test_latest_ordering_and_stem_cut():
    docs = [d(f"2026-05-0{i}T10:00:00+00:00", stem="x" * 200 if i == 3 else f"q{i}") for i in range(1, 6)] + [d()]
    r = report.build_report(docs, NOW, latest=3)
    assert [x["stem"] for x in r["latest"]] == ["q5", "q4", "x" * 90] and r["latest"][0]["created"] == "2026-05-05 13:00"


def test_cli_report_is_read_only(monkeypatch, capsys):
    from sat_engine import pipeline
    monkeypatch.setattr(report, "fetch_docs", lambda: [d("2026-05-10T10:00:00+00:00")])
    monkeypatch.setattr(pipeline, "run_session", lambda *a, **k: pytest.fail("run_session reached"))
    assert main(["--report"]) == 0 and "Toplam 1" in capsys.readouterr().out
    assert main(["--report", "--json"]) == 0 and json.loads(capsys.readouterr().out)["total"] == 1
    monkeypatch.setattr(report, "fetch_docs", lambda: (_ for _ in ()).throw(OSError("no credentials")))
    assert main(["--report"]) == 1 and "rapor alınamadı" in capsys.readouterr().out


def test_report_and_ui_have_no_write_calls():
    root = Path(report.__file__).parent
    pat = re.compile(r"\.(set|add|delete|create|commit|batch|bulk_writer|transaction)\(|\.update\(\{")
    assert not pat.search((root / "report.py").read_text(encoding="utf-8"))
    ui = (root / "ui_panel.py").read_text(encoding="utf-8")
    assert not re.search(r"get_db|firestore|firebase_admin|\.collection\(|\.document\(", ui)  # UI only calls report.fetch_docs


def test_error_messages():
    assert "kimlik" in ui_panel.friendly_error(FileNotFoundError("[Errno 2] No such file"))
    assert "çevrimdışı" in ui_panel.friendly_error(RuntimeError("503 Unavailable"))


def test_window_smoke(tmp_path):
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        w = ui_panel.ReportWindow(root, fetch=lambda: [d("2026-05-10T10:00:00+00:00")])
        for _ in range(60):
            root.update()
            if w.report:
                break
            import time
            time.sleep(0.05)
        assert w.report and w.cards["total"].cget("text") == "1" and len(w.t_last.get_children()) == 1
        w.destroy()
        root.update()
        w2 = ui_panel.ReportWindow(root, fetch=lambda: (_ for _ in ()).throw(OSError("offline Unavailable")))
        for _ in range(60):
            root.update()
            if "çevrimdışı" in w2.status.get():
                break
            import time
            time.sleep(0.05)
        assert "çevrimdışı" in w2.status.get()
        w2.destroy()
        root.update()
    finally:
        root.destroy()


def test_key_letters_other_bucket():
    r = report.build_report([d(key="A"), d(key="E"), d(key="")], NOW)
    assert r["key_letters"] == {"A": 1, "B": 0, "C": 0, "D": 0, "other": 2}


def test_poll_survives_show_error():
    import time
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        w = ui_panel.ReportWindow(root, fetch=lambda: [d("2026-05-10T10:00:00+00:00")])
        w.show = lambda r: (_ for _ in ()).throw(ValueError("boom"))
        for _ in range(60):
            root.update()
            if "gösterilemedi" in w.status.get():
                break
            time.sleep(0.05)
        assert "gösterilemedi" in w.status.get() and w._job is not None
        w.destroy()
        root.update()
    finally:
        root.destroy()
