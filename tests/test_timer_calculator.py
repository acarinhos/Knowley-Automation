import unittest
from core.timer_calculator import (
    calculate_question_durations,
    calculate_question_duration,
    calculate_durations_from_obj,
    calculate_duration_from_obj,
)
from core.generator import (
    GeneratedQuestion,
    LocalizedContent,
    Options,
    DifficultyProfile,
    DifficultyScope
)


class TestTimerCalculator(unittest.TestCase):

    def test_distinct_local_and_global_durations(self):
        """
        Yerel: Kolay (+0), Global: Zor (+5) senaryosu:
        Toplam Karakter: 50
        Okuma süresi: round(50/25) = 2
        Yerel Süre: 10 + 2 + 0 = 12s
        Global Süre: 10 + 2 + 5 = 17s
        """
        q_text = "A" * 26
        options = ["A" * 6, "A" * 6, "A" * 6, "A" * 6]  # Total: 50 chars
        durations = calculate_question_durations(
            question_text=q_text,
            options=options,
            difficulty_local="Kolay",
            difficulty_global="Zor"
        )
        self.assertEqual(durations["duration_local"], 12)
        self.assertEqual(durations["duration_global"], 17)
        self.assertEqual(durations["duration_seconds"], 12)

    def test_example_1_kolay_short(self):
        """Örnek 1: Kolay, kısa soru (50 kar.) -> 10 + 2 + 0 = 12 saniye"""
        q_text = "A" * 26
        options = ["A" * 6, "A" * 6, "A" * 6, "A" * 6]
        duration = calculate_question_duration(q_text, options, difficulty="Kolay")
        self.assertEqual(duration, 12)

    def test_example_2_zor_long(self):
        """Örnek 2: Zor, uzun soru (250 kar.) -> 10 + 10 + 5 = 25 saniye"""
        q_text = "A" * 150
        options = ["A" * 25, "A" * 25, "A" * 25, "A" * 25]
        duration = calculate_question_duration(q_text, options, difficulty="Zor")
        self.assertEqual(duration, 25)

    def test_min_and_max_clamps(self):
        """Alt (10s) ve üst (30s) sınır testleri"""
        dur_min = calculate_question_durations("", [], difficulty_local="Kolay", difficulty_global="Kolay")
        self.assertEqual(dur_min["duration_local"], 10)
        self.assertEqual(dur_min["duration_global"], 10)

        dur_max = calculate_question_durations("A" * 1000, ["A" * 50] * 4, difficulty_local="Zor", difficulty_global="Zor")
        self.assertEqual(dur_max["duration_local"], 30)
        self.assertEqual(dur_max["duration_global"], 30)

    def test_pydantic_generated_question_object(self):
        """Pydantic GeneratedQuestion nesnesi üzerinden yerel ve global süre testi"""
        q = GeneratedQuestion(
            categories=["Tarih"],
            sub_categories=["Cumhuriyet"],
            countries=["TR"],
            difficulty_profile=DifficultyProfile(
                local=DifficultyScope(label="Kolay", score=2),
                global_scope=DifficultyScope(label="Zor", score=9)
            ),
            correct_answer="A",
            translations={
                "tr": LocalizedContent(
                    question="Türkiye Cumhuriyeti hangi yılda ilan edilmiştir?",  # 48 chars
                    options=Options(A="1923", B="1920", C="1919", D="1924"),     # 16 chars -> Total: 64 chars
                    explanation="Cumhuriyet 29 Ekim 1923'te ilan edilmiştir.",
                    filter_tag="history"
                )
            }
        )
        # Total chars = 64 -> round(64/25) = 3
        # Local (Kolay): 10 + 3 + 0 = 13s
        # Global (Zor): 10 + 3 + 5 = 18s
        durations = calculate_durations_from_obj(q)
        self.assertEqual(durations["duration_local"], 13)
        self.assertEqual(durations["duration_global"], 18)
        self.assertEqual(durations["duration_seconds"], 13)
        self.assertEqual(calculate_duration_from_obj(q), 13)

    def test_dict_object_with_separate_profiles(self):
        """Firestore dict dokümanı üzerinden yerel/küresel ayrık süre testi"""
        doc_dict = {
            "difficulty_profile": {
                "local": {"label": "Orta", "score": 5},
                "global": {"label": "Zor", "score": 8}
            },
            "translations": {
                "tr": {
                    "question": "A" * 100,
                    "options": {"A": "B" * 25, "B": "B" * 25, "C": "B" * 25, "D": "B" * 25}
                }
            }
        }
        # Total chars = 200 -> reading extra = round(200/25) = 8
        # Local (Orta): 10 + 8 + 2 = 20s
        # Global (Zor): 10 + 8 + 5 = 23s
        durations = calculate_durations_from_obj(doc_dict)
        self.assertEqual(durations["duration_local"], 20)
        self.assertEqual(durations["duration_global"], 23)
        self.assertEqual(durations["duration_seconds"], 20)


if __name__ == "__main__":
    unittest.main()
