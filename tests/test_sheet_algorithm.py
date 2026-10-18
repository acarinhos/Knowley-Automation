import unittest

from core.generation_planner import GenerationPlanner
from core.generator import GeneratedQuestion, QUESTION_SCHEMA_VERSION, build_prompt
from core.sheet_algorithm import SheetAlgorithmRepository, parse_scope_ratio
from worker import GenerationOptions, _choose_sheet_localization_country


class SheetAlgorithmRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = SheetAlgorithmRepository()

    def test_snapshot_has_expected_algorithm_dimensions(self):
        summary = self.repository.summary()
        self.assertTrue(summary["source"]["version"].endswith("1.0.5"))
        self.assertEqual(summary["category_count"], 21)
        self.assertEqual(summary["country_count"], 32)  # includes Portekiz (PT)
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
            if country is None:
                self.assertNotIn(("Spor", "Localize Sub"), observed)
                self.assertNotIn(("Müzik", "Localize Sub"), observed)
                self.assertNotIn(("Edebiyat", "Localize Sub"), observed)
                expected_without_placeholders = {
                    item for item in expected if item[1] != "Localize Sub"
                }
                self.assertTrue(expected_without_placeholders <= observed)
            else:
                self.assertEqual(observed, expected, country)

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

    def test_guidance_rows_carry_their_algorithm_section(self):
        self.assertEqual(
            self.repository.target("Tarih", "Temel Tarih").section,
            "section_1",
        )
        self.assertEqual(
            self.repository.target("Sinema", "Temel Sinema").section,
            "section_2",
        )

    def test_section_two_is_focus_only_without_country_profile(self):
        planner = GenerationPlanner(sheet_repository=self.repository)
        plan = planner.create_plan("Sinema", "Yerli Üretimler")
        self.assertEqual(plan.scope, "global")
        self.assertEqual(plan.ratio_percent, 100)
        self.assertEqual(plan.target_country, "Global")

    def test_country_profile_does_not_change_section_two_element_targets(self):
        target = self.repository.target("Sinema", "Yerli Üretimler", country="Türkiye")
        self.assertEqual(target.source_sheet, "Türkiye Kategorizasyon")
        self.assertEqual(target.section, "section_2")
        planner = GenerationPlanner(sheet_repository=self.repository)
        plan = planner.create_plan(
            "Sinema",
            "Yerli Üretimler",
            preferred_local_country="Türkiye",
        )
        self.assertEqual((plan.scope, plan.target_country), ("global", "Global"))

    def test_country_profile_only_applies_to_local_part_of_section_one_ratio(self):
        planner = GenerationPlanner(sheet_repository=self.repository)
        plans = [
            planner.create_plan(
                "Genel Kültür",
                "Kültürel İkonlar",
                preferred_local_country="Türkiye",
            )
            for _ in range(20)
        ]
        global_plans = [plan for plan in plans if plan.scope == "global"]
        local_plans = [plan for plan in plans if plan.scope == "local"]
        self.assertEqual(len(global_plans), 3)
        self.assertEqual(len(local_plans), 17)
        self.assertEqual({plan.target_country for plan in global_plans}, {"Global"})
        self.assertEqual({plan.target_country for plan in local_plans}, {"Türkiye"})

    def test_country_profile_replaces_only_section_three_categories(self):
        records = self.repository.records_for_country("Türkiye")
        cinema = [record for record in records if record["category"] == "Sinema"]
        sport = [record for record in records if record["category"] == "Spor"]
        self.assertEqual({record["source_sheet"] for record in cinema}, {"Türkiye Kategorizasyon"})
        self.assertEqual({record["source_sheet"] for record in sport}, {"Türkiye Kategorizasyon"})

    def test_section_one_preserves_sheet_ratio_and_uses_sheet_countries(self):
        planner = GenerationPlanner(sheet_repository=self.repository)
        target = self.repository.target("Tarih", "Temel Tarih")
        scopes = [planner.create_plan("Tarih", "Temel Tarih").scope for _ in range(20)]
        self.assertEqual(scopes.count("global"), round(target.global_ratio * 20))
        self.assertEqual(scopes.count("local"), round(target.local_ratio * 20))

    def test_section_three_expands_to_real_country_subcategories_with_exact_credits(self):
        for selected_country in ("Türkiye", "Japonya", "Portekiz"):
            for pool in ("154", "64"):
                queue = self.repository.build_credit_queue(pool, country=selected_country)
                for category in ("Spor", "Müzik", "Edebiyat"):
                    targets = [target for target in queue if target.category == category]
                    self.assertTrue(targets)
                    self.assertNotIn("Localize Sub", {target.subcategory for target in targets})
                    expected = {
                        record["subcategory"]: record[f"weight_{pool}"]
                        for record in self.repository.records_for_country(selected_country)
                        if record["category"] == category and record[f"weight_{pool}"] > 0
                    }
                    observed = {}
                    for target in targets:
                        observed[target.subcategory] = observed.get(target.subcategory, 0) + 1
                        self.assertTrue(target.guide or target.section)
                    self.assertEqual(observed, expected, (selected_country, pool, category))

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
        names = [item["name"] for item in self.repository.country_profiles()]
        self.assertEqual(len(names), 31)
        self.assertEqual(names[:2], ["Türkiye", "ABD"])
        for expected in ("Portekiz", "Latin Amerika", "Balkanlar"):
            self.assertIn(expected, names)
        self.assertNotIn("Doğu & Güney Afrika", names)  # no profile sheet yet

    def test_portugal_is_a_country_with_pt_and_en(self):
        portugal = next(c for c in self.repository.countries() if c["code"] == "PT")
        self.assertEqual((portugal["name"], portugal["languages"]), ("Portekiz", ["pt", "en"]))
        self.assertEqual(portugal["profile_sheet"], "Portekiz Kategorizasyon")
        self.assertEqual(self.repository.question_languages("Portekiz"), ["pt", "en"])
        self.assertEqual(self.repository.country_code("Portekiz", "local"), "PT")
        self.assertTrue(self.repository.records_for_country("Portekiz"))

    def test_portugal_localize_sub_credits_are_read(self):
        records = self.repository._localized_records["Portekiz"]
        subs = {c: [r["subcategory"] for r in records if r["category"] == c] for c in ("Edebiyat", "Spor", "Müzik")}
        self.assertIn("Fado", subs["Müzik"])
        self.assertIn("Futbol (Liga Portugal)", subs["Spor"])
        self.assertIn("Camões & Keşifler Çağı Edebiyatı", subs["Edebiyat"])
        for category, expected in (("Edebiyat", (7, 2)), ("Spor", (10, 6)), ("Müzik", (10, 6))):
            got = (sum(r["weight_154"] for r in records if r["category"] == category),
                   sum(r["weight_64"] for r in records if r["category"] == category))
            self.assertEqual(got, expected, category)

    def test_portugal_planner_picks_portuguese_local_subcategories(self):
        planner = GenerationPlanner(sheet_repository=self.repository)
        plan = planner.create_plan("Müzik", "Fado", force_scope="local", force_country="Portekiz")
        self.assertEqual((plan.scope, plan.target_country), ("local", "Portekiz"))

    def test_every_country_profile_queue_has_exact_pool_totals(self):
        for item in self.repository.country_profiles():
            for pool in ("154", "64"):
                queue = self.repository.build_credit_queue(pool, country=item["name"])
                self.assertEqual(len(queue), int(pool), f"{item['name']} {pool}")

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

    def test_disabled_country_profile_uses_automatic_sheet_context(self):
        options = GenerationOptions(
            generation_mode="basic",
            country="Türkiye",
            scope="local",
            country_profile_enabled=False,
        ).validate()
        self.assertEqual(options.generation_mode, "basic")
        self.assertEqual(options.credit_pool, "154")
        self.assertIsNone(options.country)
        self.assertIsNone(options.scope)

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

    def test_localize_sub_country_comes_from_sheet_country_region_list(self):
        repository = SheetAlgorithmRepository()
        allowed = {item["name"] for item in repository.countries()}
        selected = {_choose_sheet_localization_country(repository) for _ in range(100)}
        self.assertTrue(selected <= allowed)
        self.assertGreater(len(selected), 1)


if __name__ == "__main__":
    unittest.main()
