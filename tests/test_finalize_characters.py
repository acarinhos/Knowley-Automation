import json
import unittest
from pathlib import Path

from core.character_catalog import (
    CHARACTER_LOCALES, LEVEL_THRESHOLDS, POINTS_SOURCE, default_avatar_url, firestore_character_document,
    level_from_points, load_global_character_catalog,
)
from core.translation_service import SUPPORTED_LANGUAGE_CODES
from scripts.finalize_characters import DELETE, merge_skills, migrate_translations, plan_character_update

ROOT = Path(__file__).resolve().parents[1]


class PointsAndLevelsTests(unittest.TestCase):
    def test_level_from_points(self):
        cases = {-5: 0, 0: 0, 49: 0, 50: 1, 99: 1, 100: 2, 199: 2, 200: 3, 5000: 3, None: 0, "x": 0, 49.9: 0}
        for points, level in cases.items():
            self.assertEqual(level_from_points(points), level, points)
        self.assertEqual(level_from_points(120, {"lvl1": 10, "lvl2": 20, "lvl3": 30}), 3)

    def test_contract_constants(self):
        self.assertEqual(LEVEL_THRESHOLDS, {"lvl1": 50, "lvl2": 100, "lvl3": 200})
        self.assertEqual(POINTS_SOURCE, "users/{uid}.categoryTotalScores.{category_name}")
        self.assertEqual(default_avatar_url("b"), "https://storage.googleapis.com/b/characters/default_robot.png")
        self.assertEqual(CHARACTER_LOCALES, SUPPORTED_LANGUAGE_CODES)
        self.assertTrue(all("-" not in code for code in CHARACTER_LOCALES if code not in ("zh-TW", "es-LA")))

    def test_avatar_png_exists_and_is_square(self):
        from PIL import Image

        image = Image.open(ROOT / "data" / "default_robot.png")
        self.assertEqual(image.size, (512, 512))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((0, 0))[3], 0)  # transparent background


class MigrationTests(unittest.TestCase):
    def test_translations_move_to_short_codes_and_short_key_wins(self):
        current = {"fr-FR": {"name": "old", "title": "T-fr-FR", "extra": "keep"}, "fr": {"name": "short", "title": ""},
                   "zh-CN": {"name": "n"}, "zh-TW": {"name": "tw"}, "es-419": {"name": "la"}}
        expected = {"fr": {"name": "NEW", "title": "NT", "role": "NR"}, "zh": {"name": "ZN", "title": "a", "role": "b"},
                    "zh-TW": {"name": "TW", "title": "a", "role": "b"}, "es-LA": {"name": "LA", "title": "a", "role": "b"}}
        merged = migrate_translations(current, expected)
        self.assertEqual(set(merged), {"fr", "zh", "zh-TW", "es-LA"})
        self.assertEqual(merged["fr"], {"name": "NEW", "title": "NT", "role": "NR", "extra": "keep"})
        self.assertNotIn("fr-FR", merged)

    def test_merge_skills_catalogue_wins_and_extras_survive(self):
        merged = merge_skills([{"skill_id": "a", "name": "old", "runtime_flag": 1}],
                              [{"skill_id": "a", "name": "new"}, {"skill_id": "b", "name": "b"}])
        self.assertEqual(merged, [{"skill_id": "a", "name": "new", "runtime_flag": 1}, {"skill_id": "b", "name": "b"}])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.expected = firestore_character_document(load_global_character_catalog()[0])

    def legacy(self):
        data = json.loads(json.dumps(self.expected))
        data.update({"rarity": "unassigned", "is_active": False, "avatar_url": None, "unlock_threshold": None, "name": "Original",
                     "schema_version": "1.0.0"})
        data.pop("level_thresholds", None)
        data["translations"] = {"de-DE": data["translations"]["de"], **{k: v for k, v in data["translations"].items() if k != "de"}}
        return data

    def test_legacy_document_gets_every_final_change(self):
        update = plan_character_update(self.legacy(), self.expected)
        self.assertEqual(update["rarity"], DELETE)
        self.assertIs(update["is_active"], True)
        self.assertEqual(update["unlock_threshold"], 50)
        self.assertEqual(update["level_thresholds"], LEVEL_THRESHOLDS)
        self.assertEqual(update["avatar_url"], default_avatar_url())
        self.assertEqual(update["name"], self.expected["name"])
        self.assertEqual(set(update["translations"]), set(CHARACTER_LOCALES))
        self.assertNotIn("id", update)  # ids never change

    def test_final_document_needs_nothing(self):
        self.assertEqual(plan_character_update(json.loads(json.dumps(self.expected)), self.expected), {})


class CatalogueConsistencyTests(unittest.TestCase):
    def test_no_rarity_and_short_codes_everywhere(self):
        for character in load_global_character_catalog():
            document = firestore_character_document(character)
            self.assertNotIn("rarity", document)
            self.assertEqual(set(document["translations"]), set(CHARACTER_LOCALES))
        locales = json.loads((ROOT / "data" / "global_character_locales.json").read_text(encoding="utf-8"))
        self.assertTrue(all(set(v) == set(CHARACTER_LOCALES) for v in locales.values()))


if __name__ == "__main__":
    unittest.main()
