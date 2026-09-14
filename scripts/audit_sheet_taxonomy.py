"""Repository çıktılarının kaynak Sheet adlarını birebir koruduğunu denetler."""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
SNAPSHOT_PATH = ROOT_DIR / "data" / "knowley_algorithm_snapshot.json"
PROFILE_SHEETS = [
    "Türkiye Kategorizasyon",
    "ABD Kategorizasyon",
    "Çin Kategorizasyon",
    "Japonya Kategorizasyon",
    "Almanya Kategorizasyon",
]


def taxonomy(rows: list[dict[str, Any]]) -> "OrderedDict[str, list[str]]":
    result: "OrderedDict[str, list[str]]" = OrderedDict()
    current_category = ""
    for row in rows:
        raw_category = str(row.get("Ana Kategori") or "").strip()
        if raw_category:
            current_category = raw_category
        subcategory = str(row.get("Alt Kategori (Sub)") or "").strip()
        if current_category and subcategory and current_category.upper() not in {"TOPLAM", "TOTAL"}:
            result.setdefault(current_category, []).append(subcategory)
    return result


def audit(snapshot_path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    from core.sheet_algorithm import SheetAlgorithmRepository

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    sheets = {sheet["title"]: sheet.get("rows", []) for sheet in payload.get("sheets", [])}
    repository = SheetAlgorithmRepository(snapshot_path)
    sources = {"Sub Kredi Dereceleri": taxonomy(sheets["Sub Kredi Dereceleri"])}
    sources.update({name: taxonomy(sheets[name]) for name in PROFILE_SHEETS})
    mismatches: dict[str, Any] = {}
    country_by_sheet = {sheet: country for country, sheet in zip(
        ["Türkiye", "ABD", "Çin", "Japonya", "Almanya"], PROFILE_SHEETS
    )}
    for sheet_name, source in sources.items():
        country = country_by_sheet.get(sheet_name)
        produced = OrderedDict()
        for record in repository.records_for_country(country):
            produced.setdefault(record["category"], []).append(record["subcategory"])
        if produced != source:
            mismatches[sheet_name] = {"source": source, "repository": produced}
    return {"sources": sources, "mismatches": mismatches}


def main() -> None:
    result = audit()
    print(json.dumps(result["mismatches"], ensure_ascii=False, indent=2))
    if result["mismatches"]:
        raise SystemExit(1)
    print("Genel havuz ve 5 ülke profili kaynak Sheet adlarını birebir koruyor.")


if __name__ == "__main__":
    main()
