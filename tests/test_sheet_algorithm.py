import unittest

from core.generation_planner import GenerationPlanner
from core.generator import GeneratedQuestion, QUESTION_SCHEMA_VERSION, build_prompt
from core.sheet_algorithm import SheetAlgorithmRepository, parse_scope_ratio
from worker import GenerationOptions


class SheetAlgorithmRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = SheetAlgorithmRepository()

    def test_snapshot_has_expected_algorithm_dimensions(self):
        summary = self.repository.summary()
        self.assertEqual(summary["source"]["version"], "1.0.5")
        self.assertEqual(summary["category_count"], 21)
        self.assertEqual(summary["country_count"], 20)
        self.assertEqual(summary["pool_totals"], {"154": 154, "64": 64})

    def test_sheet_category_names_are_preserved_exactly(self):
        weights = self.repository.category_weights("154")
        self.assertIn("Genel Kültür", weights)
        self.assertIn("Mitoloji", weights)
        self.assertIn("Sinema", weights)
        self.assertNotIn("Genel Kültür & Mitoloji", weights)
        self.assertNotIn("Sinema & Dizi", weights)
        self.assertEqual(sum(weights.values()), 154)

    def test_every_pool_and_country_profile_preserves_its_sheet_names(self):
        for country in [None, "Türkiye", "ABD", "Çin", "Japonya", "Almanya"]:
            records = self.repository.records_for_country(country)
            queue = self.repository.build_credit_queue("154", country=country)
            expected = {
                (record["category"], record["subcategory"])
                for record in records
                if record["weight_154"] > 0
            }
            observed = {(target.category, target.subcategory) for target in queue}
            self.assertEqual(observed, expected, country or "genel havuz")

    def test_category_selection_completes_exact_credit_cycles(self):
        for pool in ("154", "64"):
            expected = self.repository.category_weights(pool)
            observed = {}
            for _ in range(sum(expected.values())):
                category = self.repository.choose_category(observed, pool=pool)
                observed[category] = observed.get(category, 0) + 1
            self.assertEqual(observed, expected)

    def test_zero_credit_subcategories_are_not_selected(self):
        category = "Spor"
        positive = {
            record["subcategory"]
            for record in self.repository.subcategories(category)
            if record["weight_64"] > 0
        }
        counts = {}
        for _ in range(64):
            chosen = self.repository.choose_subcategory(category, counts, pool="64")
            self.assertIn(chosen, positive)
            counts[chosen] = counts.get(chosen, 0) + 1

    def test_country_specific_local_ratio_is_preserved(self):
        target = self.repository.target("Spor", "Futbol (Süper Lig)", country="Türkiye")
        self.assertEqual(target.source_sheet, "Türkiye Kategorizasyon")
        self.assertEqual(target.global_ratio, 0.0)
        self.assertEqual(target.local_ratio, 1.0)
        self.assertEqual(target.subcategory_weight, 2)

    def test_planner_uses_sheet_scope_ratio(self):
        planner = GenerationPlanner(sheet_repository=self.repository)
        scopes = [
            planner.plan_scope("Spor", subcategory="Futbol (Süper Lig)", country="Türkiye")[0]
            for _ in range(20)
        ]
        self.assertEqual(scopes, ["local"] * 20)

    def test_ratio_parser_supports_percent_and_element_marker(self):
        self.assertEqual(parse_scope_ratio("%60 GL / %40 LO"), (0.6, 0.4))
        self.assertEqual(parse_scope_ratio("Öge"), (1.0, 0.0))

    def test_country_profile_list_contains_only_country_specific_sheets(self):
        self.assertEqual(
            [item["name"] for item in self.repository.country_profiles()],
            ["Türkiye", "ABD", "Çin", "Japonya", "Almanya"],
        )

    def test_basic_mode_only_selects_base_subcategories(self):
        for category in self.repository.basic_category_weights():
            chosen = self.repository.choose_subcategory(
                category, {}, pool="154", basic_only=True
            )
            record = self.repository.find_record(category, chosen)
            self.assertIsNotNone(record)
            self.assertTrue(record["is_base"], f"{category} / {chosen} temel değil")


class GenerationOptionsTests(unittest.TestCase):
    def test_preserves_advanced_controls_but_forces_automatic_duration(self):
        options = GenerationOptions(
            question_count=12,
            difficulty="Zor",
            scope="local",
            country="Türkiye",
            languages=["tr", "en"],
            duration_seconds=45,
            generation_mode="64",
            combo_percent=35,
            country_profile_enabled=True,
        ).validate()
        self.assertEqual(options.difficulty, "Zor")
        self.assertEqual(options.scope, "local")
        self.assertEqual(options.country, "Türkiye")
        self.assertEqual(options.languages, ["tr", "en"])
        self.assertIsNone(options.duration_seconds)
        self.assertEqual(options.credit_pool, "64")
        self.assertEqual(options.generation_mode, "64")
        self.assertEqual(options.combo_percent, 35)

    def test_disabled_country_profile_uses_generic_global_context(self):
        options = GenerationOptions(
            generation_mode="basic",
            country="Türkiye",
            scope="local",
            country_profile_enabled=False,
        ).validate()
        self.assertEqual(options.generation_mode, "basic")
        self.assertEqual(options.credit_pool, "154")
        self.assertIsNone(options.country)
        self.assertEqual(options.scope, "global")

    def test_generated_question_defaults_to_new_schema_version(self):
        self.assertEqual(QUESTION_SCHEMA_VERSION, "1.1.0")
        self.assertEqual(GeneratedQuestion.model_fields["version"].default, "1.1.0")
        self.assertIn(
            '"version": "1.1.0"',
            build_prompt(primary_category="Tarih", target_subcategory="Temel Tarih"),
        )
        self.assertIsNone(GeneratedQuestion.model_fields["question_image_url"].default)
        self.assertIn(
            '"question_image_url": null',
            build_prompt(primary_category="Tarih", target_subcategory="Temel Tarih"),
        )

    def test_rejects_empty_language_selection(self):
        with self.assertRaisesRegex(ValueError, "En az bir dil"):
            GenerationOptions(languages=[]).validate()


if __name__ == "__main__":
    unittest.main()
