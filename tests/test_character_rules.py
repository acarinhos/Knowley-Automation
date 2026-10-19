import json
import unittest
from datetime import datetime
from pathlib import Path

from core.character_catalog import (
    DAILY_RESET_HOUR_LOCAL, firestore_character_document, load_global_character_catalog, reset_day_key,
)
from scripts.finalize_characters import DELETE as FINALIZE_DELETE, plan_character_update
from scripts.patch_character_rules import DELETE, plan_patch

ROOT = Path(__file__).resolve().parents[1]


class DailyResetTests(unittest.TestCase):
    def test_reset_hour_is_ten_and_in_every_character(self):
        self.assertEqual(DAILY_RESET_HOUR_LOCAL, 10)
        characters = load_global_character_catalog()
        self.assertTrue(all(c.daily_reset_hour_local == 10 for c in characters))
        catalogue = json.loads((ROOT / "data" / "global_character_catalog.json").read_text(encoding="utf-8"))["characters"]
        self.assertTrue(all("display_order" not in c["source"] for c in catalogue))

    def test_reset_day_key_changes_at_ten_local(self):
        self.assertEqual(reset_day_key(datetime(2026, 3, 5, 9, 59)), "2026-03-04")
        self.assertEqual(reset_day_key(datetime(2026, 3, 5, 10, 0)), "2026-03-05")
        self.assertEqual(reset_day_key(datetime(2026, 3, 5, 23, 59)), "2026-03-05")
        self.assertEqual(reset_day_key(datetime(2026, 3, 6, 0, 30)), "2026-03-05")  # still the previous window
        self.assertEqual(reset_day_key(datetime(2026, 1, 1, 3, 0)), "2025-12-31")  # year boundary
        self.assertEqual(reset_day_key(datetime(2026, 3, 5, 15, 0), reset_hour=12), "2026-03-05")

    def test_hour_range_is_validated(self):
        from pydantic import ValidationError
        from core.character_catalog import CharacterDocument

        base = json.loads(json.dumps(firestore_character_document(load_global_character_catalog()[0])))
        for bad in (-1, 24):
            with self.assertRaises(ValidationError):
                CharacterDocument.model_validate({**base, "daily_reset_hour_local": bad})
        self.assertEqual(CharacterDocument.model_validate({**base, "daily_reset_hour_local": 4}).daily_reset_hour_local, 4)


class PatchPlanTests(unittest.TestCase):
    def test_only_the_two_changes_are_planned(self):
        current = {"name": "x", "avatar_url": "remote.webp", "avatar_urls": {"lvl1": "a"}, "schema_version": "1.2.0",
                   "source": {"sheet": "s", "display_order": "1"}}
        self.assertEqual(plan_patch(current), {"daily_reset_hour_local": 10, "source.display_order": DELETE})

    def test_already_patched_documents_are_skipped(self):
        self.assertEqual(plan_patch({"daily_reset_hour_local": 10, "source": {"sheet": "s"}}), {})
        self.assertEqual(plan_patch({"daily_reset_hour_local": 10}), {})

    def test_wrong_hour_is_corrected(self):
        self.assertEqual(plan_patch({"daily_reset_hour_local": 8}), {"daily_reset_hour_local": 10})


class FinalizeProtectionTests(unittest.TestCase):
    def setUp(self):
        self.expected = firestore_character_document(load_global_character_catalog()[0])
        self.remote = json.loads(json.dumps(self.expected))
        self.remote.update({"avatar_url": "https://remote/lvl1.webp", "avatar_urls": {"lvl1": "x"}, "schema_version": "1.1.0",
                            "rarity": "x"})

    def test_remote_avatar_and_schema_version_are_kept_by_default(self):
        update = plan_character_update(self.remote, self.expected)
        self.assertNotIn("avatar_url", update)
        self.assertNotIn("schema_version", update)
        self.assertNotIn("avatar_urls", update)
        self.assertEqual(update["rarity"], FINALIZE_DELETE)

    def test_overwrite_flag_restores_catalogue_values(self):
        update = plan_character_update(self.remote, self.expected, keep_remote=False)
        self.assertEqual(update["avatar_url"], self.expected["avatar_url"])
        self.assertEqual(update["schema_version"], self.expected["schema_version"])

    def test_missing_remote_values_are_filled_and_new_fields_added(self):
        current = {k: v for k, v in self.expected.items() if k not in ("avatar_url", "daily_reset_hour_local")}
        current["source"] = {**current["source"], "display_order": "3"}
        update = plan_character_update(current, self.expected)
        self.assertEqual(update["avatar_url"], self.expected["avatar_url"])
        self.assertEqual(update["daily_reset_hour_local"], 10)
        self.assertEqual(update["source.display_order"], FINALIZE_DELETE)


if __name__ == "__main__":
    unittest.main()
