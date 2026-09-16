"""Kredi havuzu kuyruğu, odak noktaları ve temel bilgi standartları testleri."""

import unittest
from core.sheet_algorithm import get_sheet_algorithm_repository
from core.generator import build_prompt


class CreditQueueAndFoundationalTriviaTests(unittest.TestCase):
    def setUp(self):
        self.repo = get_sheet_algorithm_repository()

    def test_queue_154_length_and_category_weights(self):
        queue = self.repo.build_credit_queue("154")
        self.assertEqual(len(queue), 154)

        cat_counts = {}
        for target in queue:
            cat_counts[target.category] = cat_counts.get(target.category, 0) + 1

        expected_weights = self.repo.category_weights("154")
        for cat, expected in expected_weights.items():
            self.assertEqual(cat_counts.get(cat, 0), expected, f"{cat} 154 havuzunda {expected} olmalıydı")

    def test_queue_64_length_and_category_weights(self):
        queue = self.repo.build_credit_queue("64")
        self.assertEqual(len(queue), 64)

        cat_counts = {}
        for target in queue:
            cat_counts[target.category] = cat_counts.get(target.category, 0) + 1

        expected_weights = self.repo.category_weights("64")
        for cat, expected in expected_weights.items():
            self.assertEqual(cat_counts.get(cat, 0), expected, f"{cat} 64 havuzunda {expected} olmalıydı")

    def test_queue_basic_length_and_coverage(self):
        queue = self.repo.build_credit_queue(basic_only=True)
        self.assertEqual(len(queue), 21)

        categories = {target.category for target in queue}
        self.assertEqual(len(categories), 21, "Sheet'teki 21 ana kategorinin tamamı temel havuzda yer almalıdır")

    def test_custom_total_is_apportioned_from_sheet_credits(self):
        queue = self.repo.build_credit_queue("154", total_count=37)
        self.assertEqual(len(queue), 37)

        actual = {}
        for target in queue:
            key = (target.category, target.subcategory)
            actual[key] = actual.get(key, 0) + 1

        records = []
        selected_localized_countries = {
            target.category: target.target_country
            for target in queue
            if target.target_country
        }
        for record in self.repo.records_for_country():
            if record["weight_154"] <= 0:
                continue
            if record["subcategory"] != "Localize Sub":
                records.append(record)
                continue
            selected_country = selected_localized_countries[record["category"]]
            records.extend(
                localized
                for localized in self.repo._localized_records[selected_country]
                if localized["category"] == record["category"]
                and localized["weight_154"] > 0
            )
        total_weight = sum(record["weight_154"] for record in records)
        for record in records:
            expected = 37 * record["weight_154"] / total_weight
            self.assertLessEqual(abs(actual.get((record["category"], record["subcategory"]), 0) - expected), 1)

    def test_category_filter_uses_only_that_categories_sheet_credits(self):
        queue = self.repo.build_credit_queue("154", total_count=25, category="Tarih")
        self.assertEqual(len(queue), 25)
        self.assertEqual({target.category for target in queue}, {"Tarih"})

    def test_prompt_includes_foundational_knowledge_and_focus_guidelines(self):
        context = "Kaynak: Test; Kapsam Kılavuzu: Mezopotamya ve antik başkentler."
        prompt = build_prompt(
            primary_category="Tarih",
            target_subcategory="Medeniyetin Doğuşu",
            target_country="Irak",
            algorithm_context=context,
        )

        # Temel bilgi ve halkın bilgisi kuralı kontrolü
        self.assertIn("TEMELDEN BAŞLAMA VE HALKIN BİLGİSİNE UYGUNLUK", prompt)
        self.assertIn("başkentini sormak", prompt)
        self.assertIn("bayrağını", prompt)

        # Odak noktası ve kılavuz kontrolü
        self.assertIn("ZORUNLU ODAK NOKTASI VE KAPSAM KILAVUZU", prompt)
        self.assertIn("Mezopotamya ve antik başkentler", prompt)


if __name__ == "__main__":
    unittest.main()
