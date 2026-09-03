import unittest
from collections import Counter
from difficulty_balancer import DifficultyBalancer, get_difficulty_balancer


class TestDifficultyBalancer(unittest.TestCase):

    def test_single_block_4_4_2_distribution(self):
        """10 soruluk tek bir blokta tam olarak 4 Kolay, 4 Orta ve 2 Zor çekilmeli."""
        balancer = DifficultyBalancer()
        results = [balancer.get_next_target() for _ in range(10)]
        counts = Counter(results)

        self.assertEqual(counts["Kolay"], 4)
        self.assertEqual(counts["Orta"], 4)
        self.assertEqual(counts["Zor"], 2)

    def test_multiple_blocks_quota_continuity(self):
        """100 çekimde (10 blok) tam olarak 40 Kolay, 40 Orta ve 20 Zor çekilmeli."""
        balancer = DifficultyBalancer()
        results = [balancer.get_next_target() for _ in range(100)]
        counts = Counter(results)

        self.assertEqual(counts["Kolay"], 40)
        self.assertEqual(counts["Orta"], 40)
        self.assertEqual(counts["Zor"], 20)

    def test_remaining_summary_format(self):
        """Kalan blok kotası özeti doğru formatta ve doğru sayılarla dönmeli."""
        balancer = DifficultyBalancer()
        # Başlangıçta toplam 10 eleman olmalı
        initial_summary = balancer.get_remaining_summary()
        self.assertEqual(initial_summary, "K:4 O:4 Z:2")

        # 1 adet çekilince toplam 9 kalmalı
        first = balancer.get_next_target()
        summary_after_one = balancer.get_remaining_summary()
        if first == "Kolay":
            self.assertEqual(summary_after_one, "K:3 O:4 Z:2")
        elif first == "Orta":
            self.assertEqual(summary_after_one, "K:4 O:3 Z:2")
        else:
            self.assertEqual(summary_after_one, "K:4 O:4 Z:1")

    def test_score_clamping_kolay(self):
        """Kolay seviye için puanlar 1-4 aralığına çekilmeli."""
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 0), 1)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 1), 1)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 3), 3)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 4), 4)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 5), 4)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", 10), 4)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", None), 2)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", "3/10"), 3)

    def test_score_clamping_orta(self):
        """Orta seviye için puanlar 5-8 aralığına çekilmeli."""
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 1), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 4), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 5), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 7), 7)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 8), 8)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 9), 8)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", None), 6)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", "6 puan"), 6)

    def test_score_clamping_zor(self):
        """Zor seviye için puanlar 9-10 aralığına çekilmeli."""
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 1), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 8), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 9), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 10), 10)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 15), 10)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", None), 9)

    def test_score_to_level(self):
        """Puanlar doğru seviye etiketine dönüştürülmeli."""
        self.assertEqual(DifficultyBalancer.score_to_level(1), "Kolay")
        self.assertEqual(DifficultyBalancer.score_to_level(4), "Kolay")
        self.assertEqual(DifficultyBalancer.score_to_level(5), "Orta")
        self.assertEqual(DifficultyBalancer.score_to_level(8), "Orta")
        self.assertEqual(DifficultyBalancer.score_to_level(9), "Zor")
        self.assertEqual(DifficultyBalancer.score_to_level(10), "Zor")

    def test_singleton_accessor(self):
        """get_difficulty_balancer singleton nesne döndürmeli."""
        b1 = get_difficulty_balancer()
        b2 = get_difficulty_balancer()
        self.assertIs(b1, b2)

    def test_generated_question_difficulty_schema(self):
        """GeneratedQuestion nesnesi yeni standart Firestore zorluk şemasını taşımalı."""
        from generator import GeneratedQuestion, populate_question_meta

        sample_payload = {
            "categories": ["Bilim"],
            "sub_categories": ["Fizik"],
            "countries": ["Global"],
            "difficulty_local": "Kolay",
            "difficulty_local_score": 3,
            "difficulty_global": "Orta",
            "difficulty_global_score": 6,
            "difficulty_profile": {
                "local": {"label": "Kolay", "score": 3},
                "global": {"label": "Orta", "score": 6}
            },
            "correct_answer": "A",
            "translations": {
                "tr": {
                    "question": "Işık hızı nedir?",
                    "options": {"A": "300.000 km/s", "B": "150.000 km/s", "C": "500.000 km/s", "D": "100.000 km/s"},
                    "explanation": "Açıklama",
                    "filter_tag": "Fizik"
                }
            }
        }
        q = GeneratedQuestion.model_validate(sample_payload)
        q = populate_question_meta(q, "Bilim")
        dump = q.model_dump(by_alias=True)

        self.assertEqual(dump["difficulty_local"], "Kolay")
        self.assertEqual(dump["difficulty_local_score"], 3)
        self.assertEqual(dump["difficulty_global"], "Orta")
        self.assertEqual(dump["difficulty_global_score"], 6)
        self.assertIn("difficulty_meta", dump)
        self.assertEqual(dump["difficulty_meta"]["local"]["level"], "Kolay")
        self.assertEqual(dump["difficulty_meta"]["local"]["score"], 3)
        self.assertEqual(dump["difficulty_meta"]["global"]["level"], "Orta")
        self.assertEqual(dump["difficulty_meta"]["global"]["score"], 6)


if __name__ == "__main__":
    unittest.main()

