import unittest
from collections import Counter
from option_balancer import OptionBalancer
from generator import (
    GeneratedQuestion,
    LocalizedContent,
    Options,
    DifficultyProfile,
    DifficultyScope,
    CategoryMeta,
    SubCategoryMeta,
)


class TestOptionBalancer(unittest.TestCase):

    def test_catch_up_phase(self):
        """
        Catch-up Modu Testi:
        A: 63, B: 5, C: 2, D: 2 durumunda:
        - A asla seçilmemelidir.
        - İlk başta en az olan C ve D seçilmelidir.
        - B, C, D 63'e ulaşana kadar sadece B, C, D seçilmelidir.
        """
        balancer = OptionBalancer(
            auto_scan=False,
            initial_counts={"A": 63, "B": 5, "C": 2, "D": 2}
        )

        self.assertFalse(balancer.is_equalized())

        # İlk 6 seçimde sadece C ve D seçilmeli (2->5 olana kadar)
        # C ve D'nin her birine 3'er eklenmesi gerekir (toplam 6 adım)
        for _ in range(6):
            target = balancer.get_next_target_option()
            self.assertIn(target, ["C", "D"], f"Beklenen C veya D iken {target} geldi!")
            self.assertNotEqual(target, "A")

        # Şimdi B=5, C=5, D=5 olmalı
        self.assertEqual(balancer.counts["B"], 5)
        self.assertEqual(balancer.counts["C"], 5)
        self.assertEqual(balancer.counts["D"], 5)
        self.assertEqual(balancer.counts["A"], 63)

        # B, C, D'yi 63'e kadar koştur (her birine 58 adet, toplam 174 adım)
        for _ in range(174):
            target = balancer.get_next_target_option()
            self.assertIn(target, ["B", "C", "D"])
            self.assertNotEqual(target, "A")

        # Şimdi tüm şıklar tam 63 olmalı ve sistem eşitlenmiş olmalı
        self.assertEqual(balancer.counts["A"], 63)
        self.assertEqual(balancer.counts["B"], 63)
        self.assertEqual(balancer.counts["C"], 63)
        self.assertEqual(balancer.counts["D"], 63)
        self.assertTrue(balancer.is_equalized())

    def test_equalized_4_cycle_mode(self):
        """
        4'lü Dengeli Döngü Modu Testi:
        Tüm şıklar eşit olduğunda, her 4 soruluk blokta A, B, C, D tam 1'er kez seçilmelidir.
        """
        balancer = OptionBalancer(
            auto_scan=False,
            initial_counts={"A": 10, "B": 10, "C": 10, "D": 10}
        )

        self.assertTrue(balancer.is_equalized())

        # 5 döngü (20 soru) test et
        for cycle_idx in range(5):
            chosen_block = []
            for _ in range(4):
                chosen_block.append(balancer.get_next_target_option())
            
            # Her 4'lü blokta her harf tam 1 kez yer almalıdır
            self.assertEqual(sorted(chosen_block), ["A", "B", "C", "D"])
            # Dağılım dengede kalmalıdır
            expected_count = 10 + (cycle_idx + 1)
            self.assertEqual(balancer.counts["A"], expected_count)
            self.assertEqual(balancer.counts["B"], expected_count)
            self.assertEqual(balancer.counts["C"], expected_count)
            self.assertEqual(balancer.counts["D"], expected_count)
            self.assertTrue(balancer.is_equalized())

    def test_multi_language_option_remapping(self):
        """
        Çoklu Dil Senkronizasyonu Testi:
        LLM'den doğru cevap 'A' geldiğinde, hedef şık 'C' olarak belirlendiğinde:
        - Tüm dillerde doğru cevap 'C'ye gitmelidir.
        - Tüm dillerdeki çeldiriciler diğer harflere ('A', 'B', 'D') aynı sırada dağılmalıdır.
        """
        balancer = OptionBalancer(
            auto_scan=False,
            initial_counts={"A": 10, "B": 5, "C": 2, "D": 2}
        )

        sample_q = GeneratedQuestion(
            categories=["Coğrafya"],
            sub_categories=["Başkentler"],
            is_combo=False,
            countries=["TR"],
            difficulty_profile=DifficultyProfile(
                local=DifficultyScope(label="Kolay", score=1),
                global_scope=DifficultyScope(label="Orta", score=4)
            ),
            correct_answer="A",
            supported_languages=["tr", "en", "es", "pt", "de"],
            translations={
                "tr": LocalizedContent(
                    question="Türkiye'nin başkenti neresidir?",
                    options=Options(A="Ankara", B="İstanbul", C="İzmir", D="Bursa"),
                    explanation="Türkiye'nin başkenti Ankara'dır.",
                    filter_tag="geography_capitals"
                ),
                "en": LocalizedContent(
                    question="What is the capital of Turkey?",
                    options=Options(A="Ankara", B="Istanbul", C="Izmir", D="Bursa"),
                    explanation="The capital of Turkey is Ankara.",
                    filter_tag="geography_capitals"
                ),
                "es": LocalizedContent(
                    question="¿Cuál es la capital de Turquía?",
                    options=Options(A="Ankara", B="Estambul", C="Esmirna", D="Bursa"),
                    explanation="La capital de Turquía es Ankara.",
                    filter_tag="geography_capitals"
                ),
                "pt": LocalizedContent(
                    question="Qual é a capital da Turquia?",
                    options=Options(A="Ancara", B="Istambul", C="Esmirna", D="Bursa"),
                    explanation="A capital da Turquia é Ancara.",
                    filter_tag="geography_capitals"
                ),
                "de": LocalizedContent(
                    question="Was ist die Hauptstadt der Türkei?",
                    options=Options(A="Ankara", B="Istanbul", C="Izmir", D="Bursa"),
                    explanation="Die Hauptstadt der Türkei ist Ankara.",
                    filter_tag="geography_capitals"
                )
            }
        )

        # Doğrudan 'C' hedefiyle dengele
        balanced_q = balancer.balance_question(sample_q, target_option="C")

        self.assertEqual(balanced_q.correct_answer, "C")
        self.assertEqual(balanced_q.correct_option, "C")

        # TR doğru cevap 'C'de mi?
        self.assertEqual(balanced_q.translations["tr"].options.C, "Ankara")
        # EN doğru cevap 'C'de mi?
        self.assertEqual(balanced_q.translations["en"].options.C, "Ankara")
        # ES doğru cevap 'C'de mi?
        self.assertEqual(balanced_q.translations["es"].options.C, "Ankara")
        # PT doğru cevap 'C'de mi?
        self.assertEqual(balanced_q.translations["pt"].options.C, "Ancara")
        # DE doğru cevap 'C'de mi?
        self.assertEqual(balanced_q.translations["de"].options.C, "Ankara")

        # Çeldiricilerin tüm dillerde aynı harfe taşındığını doğrula
        # Örneğin İstanbul/Istambul/Estambul hangi harfe gittiyse, tüm dillerde o harfte olmalıdır
        istanbul_slot = None
        for slot in ["A", "B", "D"]:
            if getattr(balanced_q.translations["tr"].options, slot) == "İstanbul":
                istanbul_slot = slot
                break

        self.assertIsNotNone(istanbul_slot)
        self.assertEqual(getattr(balanced_q.translations["en"].options, istanbul_slot), "Istanbul")
        self.assertEqual(getattr(balanced_q.translations["es"].options, istanbul_slot), "Estambul")
        self.assertEqual(getattr(balanced_q.translations["pt"].options, istanbul_slot), "Istambul")
        self.assertEqual(getattr(balanced_q.translations["de"].options, istanbul_slot), "Istanbul")

    def test_dict_input_support(self):
        """Dict formatındaki veri yapısının da dengelendiğini doğrular."""
        balancer = OptionBalancer(
            auto_scan=False,
            initial_counts={"A": 5, "B": 5, "C": 5, "D": 5}
        )

        sample_dict = {
            "correct_answer": "A",
            "translations": {
                "tr": {
                    "question": "Test soru?",
                    "options": {"A": "Doğru", "B": "Yanlış 1", "C": "Yanlış 2", "D": "Yanlış 3"}
                }
            }
        }

        balanced_dict = balancer.balance_question(sample_dict, target_option="B")
        self.assertEqual(balanced_dict["correct_answer"], "B")
        self.assertEqual(balanced_dict["correct_option"], "B")
        self.assertEqual(balanced_dict["translations"]["tr"]["options"]["B"], "Doğru")


if __name__ == "__main__":
    unittest.main()
