import unittest
from country_credit_config import (
    is_country_global_eligible,
    get_country_credit,
    get_category_ratio,
)
from production_balancer import ProductionBalancer, get_production_balancer
from generator import (
    GeneratedQuestion,
    DifficultyProfile,
    DifficultyScope,
    CategoryMeta,
    SubCategoryMeta,
    populate_question_meta,
)
from difficulty_balancer import DifficultyBalancer


class TestProductionBalancer(unittest.TestCase):
    def setUp(self):
        self.balancer = ProductionBalancer(window_size=20)

    def test_quota_exact_distribution_tarih(self):
        """Tarih kategorisinde (25% Global, 75% Local) 100 seçimde tam 25 Global, 75 Local üretilmelidir."""
        history_ratio = get_category_ratio("Tarih")
        self.assertEqual(history_ratio["global"], 25)
        self.assertEqual(history_ratio["local"], 75)

        scopes = [self.balancer.get_next_scope("Tarih") for _ in range(100)]
        global_count = scopes.count("global")
        local_count = scopes.count("local")

        self.assertEqual(global_count, 25)
        self.assertEqual(local_count, 75)

    def test_quota_exact_distribution_bilim(self):
        """Bilim kategorisinde (85% Global, 15% Local) 100 seçimde tam 85 Global, 15 Local üretilmelidir."""
        science_ratio = get_category_ratio("Bilim")
        self.assertEqual(science_ratio["global"], 85)
        self.assertEqual(science_ratio["local"], 15)

        scopes = [self.balancer.get_next_scope("Bilim") for _ in range(100)]
        global_count = scopes.count("global")
        local_count = scopes.count("local")

        self.assertEqual(global_count, 85)
        self.assertEqual(local_count, 15)

    def test_force_scope_override(self):
        """force_scope verildiğinde havuz tüketilmeksizin zorlanan kapsam dönmelidir."""
        forced_g = self.balancer.get_next_scope("Tarih", force_scope="global")
        forced_l = self.balancer.get_next_scope("Tarih", force_scope="local")
        self.assertEqual(forced_g, "global")
        self.assertEqual(forced_l, "local")

    def test_record_success_and_stats(self):
        """Kayıt ve istatistik raporu doğrulanmalıdır."""
        self.balancer.record_success("Tarih", "global")
        self.balancer.record_success("Tarih", "local")
        self.balancer.record_success("Tarih", "local")

        stats = self.balancer.get_category_stats("Tarih")
        self.assertEqual(stats["produced_global"], 1)
        self.assertEqual(stats["produced_local"], 2)
        self.assertEqual(stats["total_produced"], 3)


class TestCountryGlobalEligibility(unittest.TestCase):
    def test_tarih_eligibility(self):
        """Tarih kategorisinde Birleşik Krallık elit ve globale açık; Portekiz ve ABD globale kapalı olmalıdır."""
        self.assertTrue(is_country_global_eligible("Tarih", "Birleşik Krallık"))
        self.assertTrue(is_country_global_eligible("Tarih", "Fransa"))
        self.assertTrue(is_country_global_eligible("Tarih", "İtalya"))

        # Portekiz ve ABD Tarih matrisinde closed listesinde
        self.assertFalse(is_country_global_eligible("Tarih", "Portekiz"))
        self.assertFalse(is_country_global_eligible("Tarih", "ABD"))
        # Brezilya kredisi < 5
        self.assertFalse(is_country_global_eligible("Tarih", "Brezilya"))

    def test_sinema_eligibility(self):
        """Sinema & Dizi kategorisinde ABD açık, Türkiye kapalı olmalıdır."""
        self.assertTrue(is_country_global_eligible("Sinema & Dizi", "ABD"))
        self.assertFalse(is_country_global_eligible("Sinema & Dizi", "Türkiye"))


class TestAsymmetricQuestionModel(unittest.TestCase):
    def test_case_a_global_eligible_dual_scoring(self):
        """Case A: Küresel Elit Ülke (is_global_eligible=True) çift zorluk taşımalıdır."""
        diff_prof = DifficultyProfile(
            local=DifficultyScope(label="Kolay", score=3),
            global_scope=DifficultyScope(label="Zor", score=9)
        )
        q = GeneratedQuestion(
            categories=["Tarih"],
            sub_categories=["Dünya Tarihi"],
            target_country="ABD",
            target_country_credit=10,
            is_global_eligible=True,
            scope="local",
            version="1.0.2",
            difficulty_local="Kolay",
            difficulty_local_score=3,
            difficulty_global="Zor",
            difficulty_global_score=9,
            difficulty_profile=diff_prof,
            correct_answer="A",
            translations={
                "tr": {"question": "Q?", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "explanation": "E", "filter_tag": "T"}
            }
        )
        q = populate_question_meta(q, "Tarih")
        dumped = q.model_dump(by_alias=True)

        self.assertTrue(dumped["is_global_eligible"])
        self.assertEqual(dumped["difficulty_local"], "Kolay")
        self.assertEqual(dumped["difficulty_local_score"], 3)
        self.assertEqual(dumped["difficulty_global"], "Zor")
        self.assertEqual(dumped["difficulty_global_score"], 9)
        self.assertIsNotNone(dumped["difficulty_meta"]["global"])
        self.assertEqual(dumped["difficulty_meta"]["global"]["level"], "Zor")
        self.assertEqual(dumped["difficulty_meta"]["global"]["score"], 9)
        self.assertEqual(dumped["difficulty_meta"]["local"]["context_country"], "ABD")
        self.assertIn("global", dumped["difficulty_profile"])

    def test_case_b_not_global_eligible_single_scoring(self):
        """Case B: Globale Kapalı Ülke (is_global_eligible=False) yalnızca yerel zorluk taşımalı, global null olmalıdır."""
        diff_prof = DifficultyProfile(
            local=DifficultyScope(label="Orta", score=6),
            global_scope=None
        )
        q = GeneratedQuestion(
            categories=["Tarih"],
            sub_categories=["Avrupa Tarihi"],
            target_country="Portekiz",
            target_country_credit=4,
            is_global_eligible=False,
            scope="local",
            version="1.0.2",
            difficulty_local="Orta",
            difficulty_local_score=6,
            difficulty_global=None,
            difficulty_global_score=None,
            difficulty_profile=diff_prof,
            correct_answer="B",
            translations={
                "tr": {"question": "Q?", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "explanation": "E", "filter_tag": "T"}
            }
        )
        q = populate_question_meta(q, "Tarih")
        dumped = q.model_dump(by_alias=True)

        self.assertFalse(dumped["is_global_eligible"])
        self.assertEqual(dumped["difficulty_local"], "Orta")
        self.assertEqual(dumped["difficulty_local_score"], 6)
        self.assertIsNone(dumped["difficulty_global"])
        self.assertIsNone(dumped["difficulty_global_score"])
        self.assertIsNone(dumped["difficulty_meta"]["global"])
        self.assertEqual(dumped["difficulty_meta"]["local"]["context_country"], "Portekiz")
        self.assertIsNone(dumped["difficulty_profile"]["global"])


if __name__ == "__main__":
    unittest.main()
