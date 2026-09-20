"""Canlı Sheet ile doğrulanan 21 kategori renginin yerel sözleşmesi."""

import unittest

from config.categories_config import (
    CATEGORIES_META,
    LEGACY_CATEGORY_COLOR_NAMES,
    SHEET_CATEGORY_COLORS,
    get_category_color,
)
from core.sheet_algorithm import SheetAlgorithmRepository


class CategoryColorTests(unittest.TestCase):
    def test_all_snapshot_colors_match_current_sheet_mapping(self):
        rows = SheetAlgorithmRepository().sheet_rows("Kategorizasyon v1.0.5")
        expected = {
            row["Ana Kategori"].strip(): row["Renk Kodu (HEX)"].strip()
            for row in rows if row.get("Ana Kategori") and row.get("Renk Kodu (HEX)")
        }
        self.assertEqual(len(expected), 21)
        self.assertEqual(SHEET_CATEGORY_COLORS, expected)
        for name, color in expected.items():
            self.assertEqual(get_category_color(name), color)

    def test_legacy_category_metadata_uses_current_colors(self):
        for name, metadata in CATEGORIES_META.items():
            sheet_name = LEGACY_CATEGORY_COLOR_NAMES.get(name, name)
            self.assertEqual(metadata["color"], SHEET_CATEGORY_COLORS[sheet_name])
            self.assertEqual(get_category_color(name), metadata["color"])
        self.assertEqual(get_category_color("Mitoloji"), "#AA00FF")
        self.assertEqual(get_category_color("Genel Kültür"), "#111111")
