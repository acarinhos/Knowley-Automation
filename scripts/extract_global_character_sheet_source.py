"""Extract global characters from an exported 'Knowley Uzman Kategorizasyonu' .xlsx into global_character_source.json shape.

Usage: python -m scripts.extract_global_character_sheet_source SHEET.xlsx OUT.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts._xlsx_reader import read

OBJECT_SHEET = "Öge Bazlı Kategoriler"
GL_SHEET_PREFIX = "Ülke  Bölge Bazlı"  # xlsx drops '/' and truncates sheet names to 31 chars
REGION_SHEETS = ("Spor", "Edebiyat", "Müzik")


def cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def order(value: str) -> str | None:
    return value[:-2] if value.endswith(".0") else (value or None)


def extract(book: dict[str, list[list[str]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    gl_name = next(name for name in book if name.startswith(GL_SHEET_PREFIX))

    category = sub = ""
    for number, row in enumerate(book[OBJECT_SHEET][1:], start=2):
        category, sub = cell(row, 0) or category, cell(row, 1) or sub
        if not cell(row, 2):
            continue
        out.append({
            "source_sheet": OBJECT_SHEET, "source_row": number, "scope": "global",
            "scope_basis": "category-wide object character; inferred global", "display_order": None,
            "category": category, "subcategory": sub, "name": cell(row, 2), "role": "",
            "skills": [cell(row, i) for i in (3, 4, 5)], "quota": row[6].strip() if len(row) > 6 else "",
            "question_mix": None,
        })

    category = sub = mix = ""
    for number, row in enumerate(book[gl_name][1:], start=2):
        category, sub, mix = cell(row, 0) or category, cell(row, 1) or sub, cell(row, 2) or mix
        if cell(row, 4) != "[Global]":
            continue
        out.append({
            "source_sheet": "Ülke / Bölge Bazlı Kategoriler (GL)", "source_row": number, "scope": "global",
            "scope_basis": "explicit [Global] row", "display_order": None, "category": category,
            "subcategory": sub, "name": cell(row, 3), "role": cell(row, 5),
            "skills": [cell(row, i) for i in (6, 7, 8)], "quota": row[9].strip() if len(row) > 9 else "",
            "question_mix": mix or None,
        })

    for name in REGION_SHEETS:
        sub = ""
        for number, row in enumerate(book[name][1:], start=2):
            region = cell(row, 1)
            if region and region != "Küresel":
                break
            sub = cell(row, 2) or sub
            if not cell(row, 3):
                continue
            out.append({
                "source_sheet": name, "source_row": number, "scope": "global",
                "scope_basis": "explicit global row", "display_order": order(cell(row, 0)),
                "category": name, "subcategory": sub, "name": cell(row, 3), "role": cell(row, 4),
                "skills": [cell(row, i) for i in (5, 6, 7)], "quota": cell(row, 8), "question_mix": None,
            })
    return out


def main(argv: list[str]) -> int:
    rows = extract(read(argv[1]))
    Path(argv[2]).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(rows)} global characters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
