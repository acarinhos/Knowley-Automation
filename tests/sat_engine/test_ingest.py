import json

import pymupdf

from sat_engine.ingest import extract as ex


def test_text_extract_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "REF_DIR", tmp_path / "ref")
    monkeypatch.setattr(ex, "MANIFEST", tmp_path / "ref" / "manifest.json")
    pdf = tmp_path / "t.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "hello synthetic page")
    d.save(pdf)
    assert ex.extract(pdf, "s", "text") == []
    assert [p for p, _ in ex.iter_pages("s")] == [1]
    m = json.loads(ex.MANIFEST.read_text())["s"]["1"]
    assert m["method"] == "text" and m["chars"] > 0 and m["low_text"] is True
    (tmp_path / "ref" / "s" / "page_001.txt").write_text("kept")
    ex.extract(pdf, "s", "text")  # resume: must not overwrite
    assert next(ex.iter_pages("s"))[1] == "kept"
