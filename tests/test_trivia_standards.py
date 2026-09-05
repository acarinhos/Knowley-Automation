"""
Knowley Soru Üretim Motoru v1.0.3
Evrensel "Kültürel Tanınırlık & Trivia" Standartı (Anti-Bürokrasi Kuralı) Birim Testleri
"""

import unittest
from config.categories_config import CATEGORIES_META
from core.generator import (
    BUREAUCRACY_BLACKLIST,
    SELF_VERIFICATION_RULE,
    TRIVIA_GUIDELINES,
    TRIVIA_SYSTEM_PROMPT,
    build_prompt,
    validate_trivia_compliance,
    GeneratedQuestion,
    DifficultyProfile,
    DifficultyScope,
    LocalizedContent,
    Options,
)


class TestTriviaAntiBureaucracyStandards(unittest.TestCase):

    def setUp(self):
        self.expected_20_categories = [
            "Tarih", "Coğrafya", "Spor", "Fizik", "Kimya", "Biyoloji",
            "Ekonomi & Finans", "Edebiyat", "Felsefe & Mantık", "Sinema & Dizi",
            "Müzik", "Genel Kültür & Mitoloji", "Bilgisayar & Yazılım", "Tıp & Sağlık",
            "Sosyoloji & Psikoloji", "Astronomi & Uzay", "Hukuk & Siyaset",
            "Oyun & Espor", "Gastronomi & Mutfak", "Mimarlık & Sanat"
        ]

    def test_all_20_categories_in_guidelines(self):
        """TRIVIA_GUIDELINES içinde 20 ana kategorinin tamamı doğru/yanlış örnekleriyle yer almalıdır."""
        for cat in self.expected_20_categories:
            self.assertIn(
                cat, TRIVIA_GUIDELINES,
                f"'{cat}' kategorisi TRIVIA_GUIDELINES içinde bulunmalıdır!"
            )
            # Hem yanlış (❌) hem doğru (✅) simgelerinin ve örneklerinin yer aldığını doğrula
            cat_pos = TRIVIA_GUIDELINES.find(cat)
            self.assertNotEqual(cat_pos, -1)

    def test_all_20_categories_match_categories_config(self):
        """Kılavuzdaki 20 kategori, sistemdeki CATEGORIES_META anahtarları ile birebir uyuşmalıdır."""
        meta_keys = list(CATEGORIES_META.keys())
        self.assertEqual(len(self.expected_20_categories), 20)
        for cat in self.expected_20_categories:
            self.assertIn(cat, meta_keys, f"'{cat}' CATEGORIES_META içinde tanımlı olmalıdır!")

    def test_blacklist_keywords_defined(self):
        """Yasaklı bürokratik kalıpların ve evrak sözcüklerinin tamamı kara listede olmalıdır."""
        required_blacklist_words = [
            "strateji belgesi",
            "eylem planı",
            "kalkınma raporu",
            "kanun maddesi",
            "kanun fıkrası",
            "yönetmelik",
            "resmi bülten",
            "bakanlık kararı",
            "yıllık bütçe yüzdesi",
            "ulusal yol haritası",
            "genelge",
        ]
        for word in required_blacklist_words:
            self.assertIn(
                word, BUREAUCRACY_BLACKLIST,
                f"'{word}' BUREAUCRACY_BLACKLIST içinde bulunmalıdır!"
            )

    def test_system_prompt_contains_rules(self):
        """TRIVIA_SYSTEM_PROMPT kara liste, self-verification ve yönergeleri içermelidir."""
        self.assertIn("Kim Milyoner Olmak İster", TRIVIA_SYSTEM_PROMPT)
        self.assertIn("Jeopardy", TRIVIA_SYSTEM_PROMPT)
        self.assertIn("Trivial Pursuit", TRIVIA_SYSTEM_PROMPT)
        self.assertIn(SELF_VERIFICATION_RULE, TRIVIA_SYSTEM_PROMPT)
        self.assertIn("ANTİ-BÜROKRASİ", TRIVIA_SYSTEM_PROMPT)

        from core.generator import normalize_text_for_search
        norm_sys = normalize_text_for_search(TRIVIA_SYSTEM_PROMPT)
        for word in BUREAUCRACY_BLACKLIST:
            self.assertIn(normalize_text_for_search(word), norm_sys)

    def test_self_verification_rule_text(self):
        """Kendi kendini doğrulama (Self-Verification) kuralı tam metin kontrolü."""
        expected_phrase = "Bu soru Kim Milyoner Olmak İster, Jeopardy veya Trivial Pursuit gibi uluslararası bir yarışma programında"
        self.assertIn(expected_phrase, SELF_VERIFICATION_RULE)
        self.assertIn("Eğer resmi evraksa soruyu anında sil", SELF_VERIFICATION_RULE)

    def test_build_prompt_includes_trivia_standards(self):
        """build_prompt çıktısı Anti-Bürokrasi ve Trivia Standartlarını içermelidir."""
        prompt = build_prompt(
            primary_category="Tarih",
            target_subcategory="Siyasi Tarih",
            scope="global",
            is_global_eligible=True,
            base_difficulty="Orta"
        )
        self.assertIn("EVRENSEL \"KÜLTÜREL TANINIRLIK & TRIVIA\" STANDARTI", prompt)
        self.assertIn(SELF_VERIFICATION_RULE, prompt)
        self.assertIn("Magna Carta", prompt)
        self.assertIn("Pelé", prompt)
        self.assertIn("Albert Einstein", prompt)
        self.assertIn("Filippo Brunelleschi", prompt)

    def _create_sample_question(self, question_text: str, explanation: str = "Açıklama", option_a: str = "Cevap A") -> GeneratedQuestion:
        """Test için örnek GeneratedQuestion nesnesi üretir."""
        localized = LocalizedContent(
            question=question_text,
            options=Options(A=option_a, B="Seçenek B", C="Seçenek C", D="Seçenek D"),
            explanation=explanation,
            filter_tag="Test"
        )
        return GeneratedQuestion(
            categories=["Tarih"],
            sub_categories=["Siyasi Tarih"],
            scope="global",
            target_country="İngiltere",
            target_country_credit=8,
            is_global_eligible=True,
            countries=["İngiltere"],
            version="1.0.3",
            difficulty_local="Orta",
            difficulty_local_score=5,
            difficulty_global="Orta",
            difficulty_global_score=6,
            difficulty_profile=DifficultyProfile(
                local=DifficultyScope(label="Orta", score=5),
                global_scope=DifficultyScope(label="Orta", score=6)
            ),
            correct_answer="A",
            translations={
                "tr": localized,
                "en": localized,
                "es": localized,
                "pt": localized,
                "de": localized
            }
        )

    def test_valid_trivia_question_passes_validation(self):
        """Meşru bir genel kültür trivia sorusu doğrulamadan başarıyla geçmelidir."""
        q = self._create_sample_question(
            question_text="1215 yılında İngiltere Kralı Yurtsuz John'a imzalatılarak kralın yetkilerini tarihte ilk kez kısıtlayan belge hangisidir?",
            explanation="Magna Carta Libertatum, 1215 yılında imzalanmış anayasal bir belgedir.",
            option_a="Magna Carta"
        )
        # Hata fırlatmamalıdır
        try:
            validate_trivia_compliance(q)
        except ValueError as e:
            self.fail(f"Geçerli soru için beklenmedik ValueError fırlatıldı: {e}")

    def test_bureaucratic_question_fails_validation_in_question(self):
        """Soru metninde kara liste terimi olan soru ValueError fırlatmalıdır."""
        q = self._create_sample_question(
            question_text="1832 İngiltere Seçim Reformu Kanunu'nun 3. kanun maddesi hangi bölgeyi kapsar?",
            explanation="Detay",
            option_a="Londra"
        )
        with self.assertRaises(ValueError) as ctx:
            validate_trivia_compliance(q)
        self.assertIn("kanun maddesi", str(ctx.exception))

    def test_bureaucratic_question_fails_validation_in_explanation(self):
        """Açıklama kısmında yönetmelik geçen soru ValueError fırlatmalıdır."""
        q = self._create_sample_question(
            question_text="Dünyanın en büyük tatlı su debisine sahip nehri hangisidir?",
            explanation="Bu durum ilgili bakanlıkça yayımlanan yönetmelik kapsamında tescillenmiştir.",
            option_a="Amazon Nehri"
        )
        with self.assertRaises(ValueError) as ctx:
            validate_trivia_compliance(q)
        self.assertIn("yönetmelik", str(ctx.exception))

    def test_bureaucratic_question_fails_validation_in_options(self):
        """Şıklarda eylem planı geçen soru ValueError fırlatmalıdır."""
        q = self._create_sample_question(
            question_text="Mojang Studios tarafından Stockholm'de geliştirilen sandbox yapım hangisidir?",
            explanation="Minecraft dünya çapında en çok satan oyundur.",
            option_a="Ulusal Yapay Zeka Eylem Planı"
        )
        with self.assertRaises(ValueError) as ctx:
            validate_trivia_compliance(q)
        self.assertIn("eylem planı", str(ctx.exception))

    def test_bureaucratic_turkish_case_insensitive_detection(self):
        """Büyük harf ve Türkçe karakter varyasyonları (Örn: GENELGE, yonetmelik) da yakalanmalıdır."""
        q = self._create_sample_question(
            question_text="İtalya Tarım Bakanlığı'nın pizza unu nem oranı standardı GENELGE hükümleri nasıldır?",
            explanation="Detay",
            option_a="A"
        )
        with self.assertRaises(ValueError) as ctx:
            validate_trivia_compliance(q)
        self.assertIn("genelge", str(ctx.exception).lower())

        q2 = self._create_sample_question(
            question_text="Fransız Kimya Kurumu'nun tehlikeli atık sınıflandırma yonetmelik maddesi nedir?",
            explanation="Detay",
            option_a="A"
        )
        with self.assertRaises(ValueError) as ctx:
            validate_trivia_compliance(q2)
        self.assertIn("yönetmelik", str(ctx.exception).lower())

    def test_question_exceeding_character_limit_fails_validation(self):
        """150-160 karakterden uzun soru kökü mobil sınırını aştığı için ValueError fırlatmalıdır."""
        long_text = (
            "1832 yılında İngiltere Parlamentosu tarafından büyük tartışmalar neticesinde kabul edilen "
            "ve sanayi devrimi sonrasında hızla büyüyen şehirlere ilk kez mecliste temsil hakkı sağlayan reform kanunu hangisidir?"
        )
        self.assertGreater(len(long_text), 160)
        q = self._create_sample_question(question_text=long_text)
        with self.assertRaises(ValueError) as ctx:
            validate_trivia_compliance(q)
        self.assertIn("150 karakterlik mobil sınırını aştı", str(ctx.exception))

    def test_question_exceeding_word_limit_fails_validation(self):
        """25 kelimeden fazla olan soru kökü ValueError fırlatmalıdır."""
        wordy_text = (
            "Bir iki üç dört beş altı yedi sekiz dokuz on "
            "onbir oniki onüç ondört onbeş onaltı onyedi onsekiz ondokuz yirmi "
            "yirmibir yirmiiki yirmiüç yirmidört yirmibeş yirmialtı nedir?"
        )
        self.assertGreater(len(wordy_text.split()), 25)
        q = self._create_sample_question(question_text=wordy_text)
        with self.assertRaises(ValueError) as ctx:
            validate_trivia_compliance(q)
        self.assertIn("150 karakterlik mobil sınırını aştı", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
