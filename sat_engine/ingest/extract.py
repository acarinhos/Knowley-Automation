"""Turn reference PDFs into per-page text under sat_engine/reference/<source>/page_NNN.txt.

Scanned PDFs go through Tesseract OCR, text-layer PDFs through PyMuPDF.
Resumable: pages already written (and in the manifest) are skipped.
The output is copyrighted source material and is git-ignored.
"""
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pymupdf

REF_DIR = Path(__file__).resolve().parents[1] / "reference"
MANIFEST = REF_DIR / "manifest.json"
TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
LOW_TEXT = 200  # chars; pages below this are flagged as likely figure/blank pages

DEFAULTS = {
    "march2021": (r"C:\Users\ibr47\OneDrive\Masaüstü\İbrahim Acar\YKS 2028\March-2021-SAT-Full-Test.pdf", "ocr"),
    "sample4": (r"C:\Users\ibr47\Downloads\Sat Sınav Örneği 4.pdf", "text"),
}


def _load():
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}


def _save(m):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1), encoding="utf-8")


def _ocr(png: Path) -> str:
    exe = os.environ.get("TESSERACT_CMD", TESSERACT)
    r = subprocess.run([exe, str(png), "stdout", "-l", "eng"], capture_output=True, timeout=300)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200])
    return r.stdout.decode("utf-8", "replace")


def extract(pdf, source, method="auto", dpi=200):
    """Extract all pages of pdf into reference/<source>/. Returns list of failed page numbers."""
    out = REF_DIR / source
    out.mkdir(parents=True, exist_ok=True)
    manifest = _load()
    pages = manifest.setdefault(source, {})
    failed = []
    with pymupdf.open(pdf) as doc, tempfile.TemporaryDirectory() as tmp:
        if method == "auto":
            method = "text" if sum(len(p.get_text()) for p in list(doc)[:3]) > 300 else "ocr"
        for i, page in enumerate(doc, 1):
            f = out / f"page_{i:03d}.txt"
            if f.exists() and str(i) in pages:
                continue
            try:
                if method == "text":
                    text = page.get_text()
                else:
                    png = Path(tmp) / f"p{i}.png"
                    page.get_pixmap(dpi=dpi).save(png)
                    text = _ocr(png)
                    png.unlink()
            except Exception as e:  # keep going; page stays pending for a re-run
                print(f"{source} page {i} failed: {e}")
                failed.append(i)
                continue
            f.write_text(text, encoding="utf-8")
            pages[str(i)] = {
                "page": i, "chars": len(text.strip()), "method": method,
                "has_images": bool(page.get_images()), "low_text": len(text.strip()) < LOW_TEXT,
            }
            _save(manifest)
    return failed


def iter_pages(source):
    """Yield (page_number, text) for an extracted source, in order."""
    for f in sorted((REF_DIR / source).glob("page_*.txt")):
        yield int(f.stem.split("_")[1]), f.read_text(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ocr", help="scanned PDF path (OCR)", default=DEFAULTS["march2021"][0])
    ap.add_argument("--text", help="text-layer PDF path", default=DEFAULTS["sample4"][0])
    a = ap.parse_args()
    for src, pdf, m in (("march2021", a.ocr, "ocr"), ("sample4", a.text, "text")):
        failed = extract(pdf, src, m)
        pg = _load().get(src, {})
        print(f"{src}: {len(pg)} pages done, failed={failed}, low_text={[int(k) for k, v in pg.items() if v['low_text']]}")


if __name__ == "__main__":
    main()
