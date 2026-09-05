import unittest
from collections import Counter
from core.difficulty_balancer import DifficultyBalancer, get_difficulty_balancer, TargetDifficulty


class TestDifficultyBalancer(unittest.TestCase):

    def test_single_block_4_4_2_distribution(self):
        """10 soruluk tek bir blokta tam olarak 4 Kolay, 4 Orta ve 2 Zor çekilmeli."""
        balancer = DifficultyBalancer()
        results = [balancer.get_next_target() for _ in range(10)]
        levels = [r.level for r in results]
        counts = Counter(levels)

        self.assertEqual(counts["Kolay"], 4)
        self.assertEqual(counts["Orta"], 4)
        self.assertEqual(counts["Zor"], 2)

    def test_score_equal_distribution_in_10_block(self):
        """10 soruluk tek bir blokta 1-10 arasındaki her puan tam olarak 1 kez yer almalıdır."""
        balancer = DifficultyBalancer()
        results = [balancer.get_next_target() for _ in range(10)]
        scores = [r.score for r in results]

        self.assertEqual(len(scores), 10)
        self.assertEqual(sorted(scores), list(range(1, 11)))

        # Seviye ve puan uyumu testi
        for r in results:
            if r.level == "Kolay":
                self.assertIn(r.score, [1, 2, 3, 4])
            elif r.level == "Orta":
                self.assertIn(r.score, [5, 6, 7, 8])
            elif r.level == "Zor":
                self.assertIn(r.score, [9, 10])

    def test_multiple_blocks_quota_continuity(self):
        """100 çekimde (10 blok) tam olarak 40 Kolay, 40 Orta, 20 Zor ve her puan 10'ar kez gelmeli."""
        balancer = DifficultyBalancer()
        results = [balancer.get_next_target() for _ in range(100)]
        levels = [r.level for r in results]
        scores = [r.score for r in results]

        level_counts = Counter(levels)
        self.assertEqual(level_counts["Kolay"], 40)
        self.assertEqual(level_counts["Orta"], 40)
        self.assertEqual(level_counts["Zor"], 20)

        score_counts = Counter(scores)
        for sc in range(1, 11):
            self.assertEqual(score_counts[sc], 10, f"Puan {sc} tam 10 kez gelmeli!")

    def test_target_difficulty_backward_compatibility(self):
        """TargetDifficulty hem string olarak hem de tuple unpack olarak çalışmalı."""
        target = TargetDifficulty(level="Kolay", score=3)
        self.assertEqual(str(target), "Kolay")
        self.assertEqual(target, "Kolay")
        self.assertEqual(target.level, "Kolay")
        self.assertEqual(target.score, 3)

        lvl, sc = target
        self.assertEqual(lvl, "Kolay")
        self.assertEqual(sc, 3)

    def test_remaining_summary_format(self):
        """Kalan blok kotası özeti doğru formatta ve doğru sayılarla dönmeli."""
        balancer = DifficultyBalancer()
        initial_summary = balancer.get_remaining_summary()
        self.assertEqual(initial_summary, "K:4 O:4 Z:2")

        first = balancer.get_next_target()
        summary_after_one = balancer.get_remaining_summary()
        if first.level == "Kolay":
            self.assertEqual(summary_after_one, "K:3 O:4 Z:2")
        elif first.level == "Orta":
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
        self.assertIn(DifficultyBalancer.validate_and_clamp_score("Kolay", None), [1, 2, 3, 4])
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Kolay", "3/10"), 3)

    def test_score_clamping_orta(self):
        """Orta seviye için puanlar 5-8 aralığına çekilmeli."""
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 1), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 4), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 5), 5)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 7), 7)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 8), 8)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", 9), 8)
        self.assertIn(DifficultyBalancer.validate_and_clamp_score("Orta", None), [5, 6, 7, 8])
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Orta", "6 puan"), 6)

    def test_score_clamping_zor(self):
        """Zor seviye için puanlar 9-10 aralığına çekilmeli."""
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 1), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 8), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 9), 9)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 10), 10)
        self.assertEqual(DifficultyBalancer.validate_and_clamp_score("Zor", 15), 10)
        self.assertIn(DifficultyBalancer.validate_and_clamp_score("Zor", None), [9, 10])

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

    def test_normalize_level(self):
        """Zorluk seviyeleri ve İngilizce eşdeğerleri standart seviyelere normalize edilmelidir."""
        self.assertEqual(DifficultyBalancer.normalize_level("kolay"), "Kolay")
        self.assertEqual(DifficultyBalancer.normalize_level("Easy"), "Kolay")
        self.assertEqual(DifficultyBalancer.normalize_level("medium"), "Orta")
        self.assertEqual(DifficultyBalancer.normalize_level("Hard"), "Zor")
        self.assertEqual(DifficultyBalancer.normalize_level("Difficult"), "Zor")
        self.assertEqual(DifficultyBalancer.normalize_level("ZOR"), "Zor")
        self.assertEqual(DifficultyBalancer.normalize_level(None), "Orta")

    def test_generated_question_difficulty_schema(self):
        """GeneratedQuestion nesnesi yeni standart Firestore v1.0.3 zorluk şemasını taşımalı."""
        from core.generator import GeneratedQuestion, populate_question_meta

        sample_payload = {
            "categories": ["Fizik"],
            "sub_categories": ["Mekanik"],
            "countries": ["Global"],
            "scope": "global",
            "target_country": "Almanya",
            "target_country_credit": 10,
            "version": "1.0.3",
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
                    "question": "Yerçekimi ivmesi nedir?",
                    "options": {"A": "9.8 m/s²", "B": "5.0 m/s²", "C": "1.0 m/s²", "D": "12.2 m/s²"},
                    "explanation": "Açıklama",
                    "filter_tag": "Mekanik"
                }
            }
        }
        q = GeneratedQuestion.model_validate(sample_payload)
        q = populate_question_meta(q, "Fizik")
        dump = q.model_dump(by_alias=True)

        self.assertEqual(dump["version"], "1.0.3")
        self.assertEqual(dump["scope"], "global")
        self.assertEqual(dump["target_country"], "Almanya")
        self.assertEqual(dump["target_country_credit"], 10)
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
