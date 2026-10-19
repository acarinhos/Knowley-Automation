import unittest
from unittest.mock import MagicMock

from config.categories_config import SHEET_CATEGORY_COLORS
from core.category_ids import (
    AMBIGUOUS_LEGACY, CURRENT_CATEGORIES, LEGACY_TO_CURRENT, apply_category_ids, category_ids_for, desired_category_fields,
    resolve_category,
)
from core.character_catalog import slug_id
from scripts.backfill_category_ids import classify_changes, dictionary_report, version_group
from scripts.export_global_character_runtime_map import PLAYER_READS


class ResolveTests(unittest.TestCase):
    def test_current_legacy_ambiguous_and_unknown(self):
        self.assertEqual(resolve_category("Tarih"), ("current", "Tarih"))
        self.assertEqual(resolve_category("Astronomi & Uzay"), ("mapped", "Astronomi"))
        self.assertEqual(resolve_category("Mimarlık & Sanat"), ("mapped", "Resim"))
        self.assertEqual(resolve_category("Bilim"), ("ambiguous", None))
        self.assertEqual(resolve_category("Matematik"), ("unmapped", None))
        self.assertEqual(resolve_category(None), ("unmapped", None))

    def test_mapping_targets_exist_and_never_collide_with_ambiguous_names(self):
        self.assertEqual(len(CURRENT_CATEGORIES), 21)
        self.assertTrue(set(LEGACY_TO_CURRENT.values()) <= set(CURRENT_CATEGORIES))
        self.assertFalse(set(LEGACY_TO_CURRENT) & set(AMBIGUOUS_LEGACY))
        self.assertFalse(set(LEGACY_TO_CURRENT) & set(CURRENT_CATEGORIES))


class IdTests(unittest.TestCase):
    def test_ids_use_the_character_slug_and_are_parallel(self):
        ids, sub_ids = category_ids_for(["Genel Kültür", "Sağlık"], ["Temel Sağlık & Anatomi"])
        self.assertEqual(ids, ["genel_kultur", "saglik"])
        self.assertEqual(sub_ids, [slug_id("Temel Sağlık & Anatomi")])
        self.assertEqual(ids[0], slug_id("Genel Kültür"))  # same slug method as the characters

    def test_apply_adds_fields_and_reports_change(self):
        question = {"categories": ["Sinema"], "sub_categories": ["Temel Sinema", "Yerli Üretimler"]}
        self.assertTrue(apply_category_ids(question))
        self.assertEqual((len(question["category_ids"]), len(question["sub_category_ids"])), (1, 2))
        self.assertFalse(apply_category_ids(question))  # idempotent
        empty = {}
        apply_category_ids(empty)
        self.assertEqual((empty["category_ids"], empty["sub_category_ids"]), ([], []))

    def test_dictionary_has_no_slug_collisions(self):
        from core.sheet_algorithm import get_sheet_algorithm_repository

        report = dictionary_report(get_sheet_algorithm_repository())
        self.assertEqual(len(report["categories"]), 21)
        self.assertEqual(report["slug_collisions"], {})
        self.assertEqual(report["empty_slugs"], [])


class PlanTests(unittest.TestCase):
    def test_legacy_name_is_mapped_with_colour_and_ids(self):
        data = {"categories": ["Sinema & Dizi"], "categories_meta": [{"name": "Sinema & Dizi", "color": "#000000"}],
                "sub_categories": ["Gişe"]}
        plan = desired_category_fields(data)
        self.assertEqual(plan["status"], "ok")
        self.assertEqual(plan["set"]["categories"], ["Sinema"])
        self.assertEqual(plan["set"]["categories_meta"], [{"name": "Sinema", "color": SHEET_CATEGORY_COLORS["Sinema"]}])
        self.assertEqual(plan["set"]["category_ids"], ["sinema"])
        self.assertEqual(plan["set"]["sub_category_ids"], [slug_id("Gişe")])
        kinds = classify_changes(data, plan["set"])
        self.assertTrue(kinds["name_fix"] and kinds["ids_added"])

    def test_current_document_only_gets_ids_and_a_correct_document_needs_nothing(self):
        data = {"categories": ["Tarih"], "categories_meta": [{"name": "Tarih", "color": SHEET_CATEGORY_COLORS["Tarih"]}],
                "sub_categories": ["Temel Tarih"]}
        plan = desired_category_fields(data)
        self.assertEqual(set(plan["set"]), {"category_ids", "sub_category_ids"})
        data.update(plan["set"])
        self.assertEqual(desired_category_fields(data), {"status": "ok", "reason": "", "set": {}})

    def test_wrong_colour_is_a_colour_only_fix(self):
        data = {"categories": ["Tarih"], "categories_meta": [{"name": "Tarih", "color": "#8B4513"}], "sub_categories": [],
                "category_ids": ["tarih"], "sub_category_ids": []}
        plan = desired_category_fields(data)
        self.assertEqual(set(plan["set"]), {"categories_meta"})
        self.assertTrue(classify_changes(data, plan["set"])["color_fix"])

    def test_ambiguous_and_unknown_names_skip_the_whole_document(self):
        for name in ("Bilim", "Popüler Kültür", "Sanat ve Edebiyat", "Matematik"):
            plan = desired_category_fields({"categories": ["Tarih", name], "sub_categories": []})
            self.assertEqual((plan["status"], plan["set"]), ("manual", {}))
            self.assertIn(name, plan["reason"])
        self.assertEqual(desired_category_fields({"categories": []})["status"], "manual")

    def test_version_groups(self):
        self.assertEqual((version_group("1.1.0"), version_group("1.0.1"), version_group("?")), ("1.1.0", "1.0.x/other", "1.0.x/other"))


class WriterAndContractTests(unittest.TestCase):
    def test_worker_save_adds_the_ids(self):
        from worker import save_worker_question

        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value.exists = False
        question = {"translations": {"en": {"question": "Category id question?"}}, "scope": "global", "correct_answer": "A",
                    "difficulty": "Orta", "categories": ["Spor"], "sub_categories": ["Temel Spor"]}
        self.assertTrue(save_worker_question(db, question))
        saved = db.collection.return_value.document.return_value.set.call_args.args[0]
        self.assertEqual(saved["category_ids"], ["spor"])
        self.assertEqual(saved["sub_category_ids"], [slug_id("Temel Spor")])

    def test_generated_question_model_has_the_id_fields(self):
        from core.generator import GeneratedQuestion

        self.assertIn("category_ids", GeneratedQuestion.model_fields)
        self.assertIn("sub_category_ids", GeneratedQuestion.model_fields)

    def test_player_state_path_is_character_progress(self):
        reads = " ".join(PLAYER_READS)
        self.assertIn("users/{uid}/characterProgress/{characterId}", reads)
        self.assertNotIn("users/{uid}/characters/", reads)


if __name__ == "__main__":
    unittest.main()
