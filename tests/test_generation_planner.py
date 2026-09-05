"""
Knowley Soru Üretim Motoru v1.0.3
Generation Planner ve Ülke Kredi Matrisi Birim Testleri
"""

import unittest
from collections import Counter

from config.country_credit_config import (
    CATEGORY_COUNTRY_MATRIX,
    normalize_category_name
)
from core.generation_planner import (
    GenerationPlanner,
    GenerationPlan,
    get_generation_planner
)
from core.difficulty_balancer import DifficultyBalancer
from core.generator import GeneratedQuestion, populate_question_meta


class TestCountryCreditConfig(unittest.TestCase):

    def test_all_20_categories_present(self):
        """Matriste tam olarak 20 ana kategori bulunmalıdır."""
        expected_categories = [
            "Tarih", "Coğrafya", "Spor", "Fizik", "Kimya", "Biyoloji",
            "Ekonomi & Finans", "Edebiyat", "Felsefe & Mantık", "Sinema & Dizi",
            "Müzik", "Genel Kültür & Mitoloji", "Bilgisayar & Yazılım", "Tıp & Sağlık",
            "Sosyoloji & Psikoloji", "Astronomi & Uzay", "Hukuk & Siyaset",
            "Oyun & Espor", "Gastronomi & Mutfak", "Mimarlık & Sanat"
        ]
        self.assertEqual(len(CATEGORY_COUNTRY_MATRIX), 20)
        for cat in expected_categories:
            self.assertIn(cat, CATEGORY_COUNTRY_MATRIX, f"{cat} matriste bulunmalıdır!")

    def test_category_ratios_sum_to_one(self):
        """Her kategorinin global ve local oranları toplamı 1.0 olmalıdır."""
        for cat, data in CATEGORY_COUNTRY_MATRIX.items():
            ratio = data.get("ratio", {})
            self.assertIn("global", ratio, f"{cat} 'global' oranına sahip olmalıdır.")
            self.assertIn("local", ratio, f"{cat} 'local' oranına sahip olmalıdır.")
            total_ratio = round(ratio["global"] + ratio["local"], 2)
            self.assertEqual(total_ratio, 1.0, f"{cat} oran toplamı 1.0 olmalıdır, bulunan: {total_ratio}")

    def test_elite_countries_credit_greater_than_or_equal_to_5(self):
        """Elit ülkelerin tümü 5 veya üzeri kredi puanına sahip olmalıdır."""
        for cat, data in CATEGORY_COUNTRY_MATRIX.items():
            elite = data.get("elite", {})
            self.assertTrue(len(elite) > 0, f"{cat} en az bir elit ülkeye sahip olmalıdır.")
            for country, credit in elite.items():
                self.assertGreaterEqual(
                    credit, 5,
                    f"{cat} kategorisinde {country} kredisi >= 5 olmalıdır! Bulunan: {credit}"
                )
                self.assertLessEqual(credit, 10, f"{country} kredisi <= 10 olmalıdır.")

    def test_closed_countries_are_lists(self):
        """Kapalı ülkeler listesi her kategoride liste olarak tanımlanmış olmalıdır."""
        for cat, data in CATEGORY_COUNTRY_MATRIX.items():
            closed = data.get("closed")
            self.assertIsInstance(closed, list, f"{cat} closed alanı liste olmalıdır.")

    def test_normalize_category_name(self):
        """Kategori aliasları standart kategori adına dönüştürülmelidir."""
        self.assertEqual(normalize_category_name("Sinema"), "Sinema & Dizi")
        self.assertEqual(normalize_category_name("Mitoloji"), "Genel Kültür & Mitoloji")
        self.assertEqual(normalize_category_name("Teknoloji"), "Bilgisayar & Yazılım")
        self.assertEqual(normalize_category_name("Tarih"), "Tarih")


class TestGenerationPlanner(unittest.TestCase):

    def setUp(self):
        self.planner = GenerationPlanner()

    def test_singleton_planner(self):
        """get_generation_planner singleton nesnesi döndürmelidir."""
        p1 = get_generation_planner()
        p2 = get_generation_planner()
        self.assertIs(p1, p2)

    def test_plan_scope_forced(self):
        """force_scope verildiğinde o kapsam ve doğru yüzde dönmelidir."""
        scope, pct = self.planner.plan_scope("Astronomi & Uzay", force_scope="global")
        self.assertEqual(scope, "global")
        self.assertEqual(pct, 95)

        scope_loc, pct_loc = self.planner.plan_scope("Astronomi & Uzay", force_scope="local")
        self.assertEqual(scope_loc, "local")
        self.assertEqual(pct_loc, 5)

    def test_plan_scope_statistical_distribution(self):
        """Kategori oranlarına göre Global/Local seçim dağılımı tutarlı olmalıdır."""
        # Astronomi & Uzay: 0.95 Global, 0.05 Local
        scopes = [self.planner.plan_scope("Astronomi & Uzay")[0] for _ in range(300)]
        global_count = scopes.count("global")
        self.assertGreater(global_count, 250, "Astronomi için global belirgin çoğunlukta olmalıdır.")

        # Tarih: 0.25 Global, 0.75 Local
        hist_scopes = [self.planner.plan_scope("Tarih")[0] for _ in range(300)]
        local_count = hist_scopes.count("local")
        self.assertGreater(local_count, 180, "Tarih için local çoğunlukta olmalıdır.")

    def test_global_scope_only_picks_elite_and_never_closed(self):
        """Global üretimde kapalı (closed) ülkeler ASLA seçilmemeli, sadece >= 5 elit ülkeler seçilmelidir."""
        for cat in CATEGORY_COUNTRY_MATRIX:
            matrix_cfg = CATEGORY_COUNTRY_MATRIX[cat]
            closed_set = set(matrix_cfg.get("closed", []))
            elite_set = set(matrix_cfg.get("elite", {}).keys())

            for _ in range(25):
                country, credit, eligible = self.planner.plan_country(cat, scope="global")
                self.assertTrue(eligible, f"Global üretimde {cat} için {country} eligible olmalıdır!")
                self.assertNotIn(
                    country, closed_set,
                    f"Global üretimde {cat} kategorisinde kapalı ülke {country} seçilemez!"
                )
                self.assertIn(
                    country, elite_set,
                    f"Global üretimde {cat} için {country} elit listede olmalıdır!"
                )
                self.assertGreaterEqual(credit, 5)

    def test_local_scope_is_not_confined_to_turkey(self):
        """Local üretim yalnızca Türkiye'ye kilitlenmemeli, yüksek kredili ülkeler de seçilmelidir."""
        # Sinema & Dizi kategorisinde ABD (10), Birleşik Krallık (8), Fransa (7), İtalya (6), Japonya (5), Türkiye (8)
        selected_countries = [
            self.planner.plan_country("Sinema & Dizi", scope="local")[0]
            for _ in range(100)
        ]
        counts = Counter(selected_countries)
        # En az 3 farklı ülke seçilmiş olmalı
        self.assertGreaterEqual(
            len(counts), 3,
            f"Local üretimde birden fazla ülke seçilmeli, bulunanlar: {list(counts.keys())}"
        )
        # Türkiye dışında da ülkeler yer almalı
        non_tr_count = sum(cnt for c, cnt in counts.items() if c != "Türkiye")
        self.assertGreater(non_tr_count, 30, "Local sorularda Türkiye harici ülkeler de seçilmelidir.")

    def test_create_plan_returns_complete_model(self):
        """create_plan geçerli bir GenerationPlan nesnesi üretmelidir."""
        plan = self.planner.create_plan(
            category="Sinema & Dizi",
            subcategory="Gişe",
            force_scope="global",
            force_country="ABD"
        )
        self.assertIsInstance(plan, GenerationPlan)
        self.assertEqual(plan.scope, "global")
        self.assertEqual(plan.ratio_percent, 70)
        self.assertEqual(plan.target_country, "ABD")
        self.assertEqual(plan.target_country_credit, 10)
        self.assertEqual(plan.category, "Sinema & Dizi")
        self.assertEqual(plan.subcategory, "Gişe")


class TestV102SchemaAndDifficulty(unittest.TestCase):

    def test_v102_firestore_payload_structure(self):
        """v1.0.3 şeması scope, target_country, target_country_credit, created_at ve 1-10 puanlarını taşımalıdır."""
        sample_payload = {
            "version": "1.0.3",
            "scope": "global",
            "target_country": "ABD",
            "target_country_credit": 10,
            "difficulty_local": "Orta",
            "difficulty_local_score": 6,
            "difficulty_global": "Kolay",
            "difficulty_global_score": 3,
            "difficulty_meta": {
                "local": {"level": "Orta", "score": 6},
                "global": {"level": "Kolay", "score": 3}
            },
            "difficulty_profile": {
                "local": {"label": "Orta", "score": 6},
                "global": {"label": "Kolay", "score": 3}
            },
            "categories": ["Sinema & Dizi"],
            "sub_categories": ["Gişe"],
            "created_at": "2026-09-05T10:15:30.123456+00:00",
            "correct_answer": "B",
            "translations": {
                "tr": {
                    "question": "En yüksek gişe hasılatına sahip film hangisidir?",
                    "options": {"A": "Titanic", "B": "Avatar", "C": "Endgame", "D": "Star Wars"},
                    "explanation": "Avatar en yüksek hasılata sahiptir.",
                    "filter_tag": "Gişe"
                }
            }
        }
        q = GeneratedQuestion.model_validate(sample_payload)
        q = populate_question_meta(q, "Sinema & Dizi")
        dump = q.model_dump(by_alias=True)

        self.assertEqual(dump["version"], "1.0.3")
        self.assertEqual(dump["scope"], "global")
        self.assertEqual(dump["target_country"], "ABD")
        self.assertEqual(dump["target_country_credit"], 10)
        self.assertEqual(dump["difficulty_local"], "Orta")
        self.assertEqual(dump["difficulty_local_score"], 6)
        self.assertEqual(dump["difficulty_global"], "Kolay")
        self.assertEqual(dump["difficulty_global_score"], 3)
        self.assertEqual(dump["difficulty_meta"]["local"]["level"], "Orta")
        self.assertEqual(dump["difficulty_meta"]["local"]["score"], 6)
        self.assertEqual(dump["difficulty_meta"]["global"]["level"], "Kolay")
        self.assertEqual(dump["difficulty_meta"]["global"]["score"], 3)
        self.assertEqual(dump["created_at"], "2026-09-05T10:15:30.123456+00:00")
        self.assertIn("shuffle_key", dump)
        self.assertIsInstance(dump["shuffle_key"], (int, float))
        self.assertGreaterEqual(dump["shuffle_key"], 0.0)
        self.assertLessEqual(dump["shuffle_key"], 1.0)

    def test_strict_difficulty_clamping_ranges(self):
        """Kolay (1-4), Orta (5-8) ve Zor (9-10) aralıkları kesin olarak işletilmelidir."""
        # Kolay aralığı
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", -5), 1)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 0), 1)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 1), 1)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 4), 4)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 5), 4)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 10), 4)

        # Orta aralığı
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 1), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 4), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 5), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 8), 8)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 9), 8)

        # Zor aralığı
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 1), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 8), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 9), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 10), 10)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 99), 10)

    def test_score_to_level_mapping(self):
        """1-4 Kolay, 5-8 Orta, 9-10 Zor etiketine haritalanmalıdır."""
        for s in [1, 2, 3, 4]:
            self.assertEqual(DifficultyBalancer.score_to_level(s), "Kolay")
        for s in [5, 6, 7, 8]:
            self.assertEqual(DifficultyBalancer.score_to_level(s), "Orta")
        for s in [9, 10]:
            self.assertEqual(DifficultyBalancer.score_to_level(s), "Zor")


if __name__ == "__main__":
    unittest.main()
