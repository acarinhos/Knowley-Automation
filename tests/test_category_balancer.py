import unittest
from core.category_balancer import CategoryBalancer
from config.categories_config import CATEGORIES_META


class TestCategoryBalancer(unittest.TestCase):

    def setUp(self):
        self.all_categories = list(CATEGORIES_META.keys())

    def test_bootstrap_initialization(self):
        """
        Bootstrap Testi:
        Tüm konfigüre edilmiş 20 kategori ve alt kategoriler başlangıçta 0 olarak sayaçlara eklenmelidir.
        """
        balancer = CategoryBalancer(auto_scan=False)

        # 20 ana kategori mevcut ve 0 mı?
        self.assertEqual(len(self.all_categories), 20)
        for cat in self.all_categories:
            self.assertIn(cat, balancer.category_counts)
            self.assertEqual(balancer.category_counts[cat], 0)

            # Alt kategoriler mevcut ve 0 mı?
            configured_subs = list(CATEGORIES_META[cat]["sub_categories"].keys())
            for sub in configured_subs:
                self.assertIn(sub, balancer.subcategory_counts[cat])
                self.assertEqual(balancer.subcategory_counts[cat][sub], 0)

        # Başlangıçta hepsi 0 olduğu için eşitlenmiş kabul edilir
        self.assertTrue(balancer.is_equalized())

    def test_catch_up_phase(self):
        """
        Catch-up (Dengeleme) Modu Testi:
        Yüksek soruya sahip kategoriler (örn. 50) varken, az soruya sahip kategoriler (örn. 5)
        öncelikli olarak seçilmeli; yüksek olanlar asla seçilmemelidir.
        """
        initial_counts = {cat: 50 for cat in self.all_categories}
        initial_counts["Fizik"] = 5
        initial_counts["Kimya"] = 5

        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts=initial_counts
        )

        self.assertFalse(balancer.is_equalized())

        # İlk seçimlerde sadece Fizik veya Kimya seçilmeli
        for _ in range(20):
            target = balancer.get_next_target(is_combo=False)
            self.assertIn(
                target.primary_category,
                ["Fizik", "Kimya"],
                f"Beklenen az sorulu kategori iken {target.primary_category} geldi!"
            )
            self.assertNotIn(
                target.primary_category,
                ["Tarih", "Spor", "Coğrafya"],
                "Çok sorusu olan kategori catch-up modunda seçilmemeliydi!"
            )

    def test_subcategory_selection(self):
        """
        Alt Kategori Seçim Testi:
        Bir ana kategori içinde soru sayısı en az olan alt kategori seçilmelidir.
        """
        initial_subs = {
            cat: {sub: 20 for sub in CATEGORIES_META[cat]["sub_categories"].keys()}
            for cat in self.all_categories
        }
        # Biyoloji altındaki Genetik'i 1 yap
        initial_subs["Biyoloji"]["Genetik"] = 1

        initial_counts = {cat: 10 for cat in self.all_categories}
        initial_counts["Biyoloji"] = 2  # Biyoloji en az soruya sahip olsun

        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts=initial_counts,
            initial_subcategory_counts=initial_subs
        )

        target = balancer.get_next_target(is_combo=False)
        self.assertEqual(target.primary_category, "Biyoloji")
        self.assertEqual(target.target_subcategory, "Genetik")

    def test_subcategory_catch_up_and_balance(self):
        """
        Alt Dallar Kendi Arasında Dengeleme Testi:
        Tarih kategorisinde Siyasi Tarih (27) yüksek iken;
        Askeri Tarih (3), Kültürel Tarih (3), Antlaşmalar (3) eşitlenene kadar
        bu az olanlar arasından rastgele seçilmeli, Siyasi Tarih asla seçilmemelidir.
        """
        initial_counts = {cat: 20 for cat in self.all_categories}
        initial_counts["Tarih"] = 2  # Tarih seçilecek

        initial_subs = {
            cat: {sub: 10 for sub in CATEGORIES_META[cat]["sub_categories"].keys()}
            for cat in self.all_categories
        }
        initial_subs["Tarih"] = {
            "Siyasi Tarih": 27,
            "Arkeoloji": 4,
            "Askeri Tarih": 3,
            "Kültürel Tarih": 3,
            "Antlaşmalar": 3
        }

        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts=initial_counts,
            initial_subcategory_counts=initial_subs
        )

        # İlk 3 seçimde Askeri Tarih, Kültürel Tarih veya Antlaşmalar seçilmeli (min=3)
        chosen_subs = []
        for _ in range(3):
            target = balancer.get_next_target(is_combo=False, auto_increment=True)
            self.assertEqual(target.primary_category, "Tarih")
            self.assertIn(target.target_subcategory, ["Askeri Tarih", "Kültürel Tarih", "Antlaşmalar"])
            self.assertNotEqual(target.target_subcategory, "Siyasi Tarih")
            chosen_subs.append(target.target_subcategory)

        # 3 seçim sonunda Askeri Tarih, Kültürel Tarih, Antlaşmalar hepsi 4 oldu
        self.assertEqual(balancer.subcategory_counts["Tarih"]["Askeri Tarih"], 4)
        self.assertEqual(balancer.subcategory_counts["Tarih"]["Kültürel Tarih"], 4)
        self.assertEqual(balancer.subcategory_counts["Tarih"]["Antlaşmalar"], 4)
        self.assertEqual(balancer.subcategory_counts["Tarih"]["Arkeoloji"], 4)

        # Bir sonraki seçimde artık Arkeoloji de adaya katılır (hepsi 4), ama Siyasi Tarih (27) hala seçilemez
        target = balancer.get_next_target(is_combo=False, auto_increment=True)
        self.assertIn(target.target_subcategory, ["Askeri Tarih", "Kültürel Tarih", "Antlaşmalar", "Arkeoloji"])
        self.assertNotEqual(target.target_subcategory, "Siyasi Tarih")

    def test_equalized_cycle_mode(self):
        """
        Döngü Modu (Equalized State) Testi:
        Tüm ana kategoriler eşitlendiğinde, karıştırılmış bir döngü havuzundan
        her kategori tam olarak 1'er kez seçilmelidir.
        """
        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts={cat: 10 for cat in self.all_categories}
        )

        self.assertTrue(balancer.is_equalized())

        # 3 tam döngü (her döngüde len(all_categories) soru)
        num_cats = len(self.all_categories)
        for _ in range(3):
            cycle_chosen = []
            for _ in range(num_cats):
                target = balancer.get_next_target(is_combo=False, auto_increment=True)
                cycle_chosen.append(target.primary_category)

            # Her döngüde her kategori tam 1 kez yer almalıdır
            self.assertEqual(sorted(cycle_chosen), sorted(self.all_categories))
            self.assertTrue(balancer.is_equalized())

    def test_combo_secondary_selection(self):
        """
        Combo (Çoklu Kategori) Seçim Testi:
        İkinci kategori, POSSIBLE_COMBO_MATCHES tablosunda yer alan
        ve soru sayısı en az olan alternatifler arasından seçilmelidir.
        """
        initial_counts = {cat: 30 for cat in self.all_categories}
        initial_counts["Fizik"] = 2
        # Fizik'in olası eşleşmeleri: ["Astronomi & Uzay", "Kimya", "Bilgisayar & Yazılım", "Felsefe & Mantık"]
        initial_counts["Kimya"] = 10

        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts=initial_counts
        )

        target = balancer.get_next_target(is_combo=True)
        self.assertEqual(target.primary_category, "Fizik")
        self.assertTrue(target.is_combo)
        # Kimya olası eşleşmeler içinde en az olan olduğu için seçilmeli
        self.assertEqual(target.secondary_category, "Kimya")

    def test_record_success_counter_updates(self):
        """
        Sayaç Güncelleme Testi:
        record_success çağrıldığında birincil, alt ve ikincil kategori sayaçları +1 artmalıdır.
        """
        balancer = CategoryBalancer(auto_scan=False)

        initial_fizik = balancer.category_counts["Fizik"]
        initial_sub = balancer.subcategory_counts["Fizik"]["Mekanik"]
        initial_kimya = balancer.category_counts["Kimya"]

        balancer.record_success(
            primary_category="Fizik",
            subcategory="Mekanik",
            secondary_category="Kimya"
        )

        self.assertEqual(balancer.category_counts["Fizik"], initial_fizik + 1)
        self.assertEqual(balancer.subcategory_counts["Fizik"]["Mekanik"], initial_sub + 1)
        self.assertEqual(balancer.category_counts["Kimya"], initial_kimya + 1)

    def test_record_question_helper(self):
        """
        record_question Yardımcı Metot Testi:
        Dict veya nesne formatındaki sorunun kategori bilgilerini otomatik okuyup sayaçları artırmalıdır.
        """
        balancer = CategoryBalancer(auto_scan=False)

        q_data = {
            "categories": ["Spor", "Tarih"],
            "sub_categories": ["Futbol"],
            "is_combo": True
        }

        balancer.record_question(q_data)

        self.assertEqual(balancer.category_counts["Spor"], 1)
        self.assertEqual(balancer.subcategory_counts["Spor"]["Futbol"], 1)
        self.assertEqual(balancer.category_counts["Tarih"], 1)

    def test_distribution_summary(self):
        """
        Dağılım Özeti Testi:
        get_distribution_summary doğru formatta istatistik ve özet döndürmelidir.
        """
        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts={cat: 5 for cat in self.all_categories}
        )

        summary = balancer.get_distribution_summary()
        self.assertIn("total_questions", summary)
        self.assertEqual(summary["total_questions"], len(self.all_categories) * 5)
        self.assertTrue(summary["is_equalized"])
        self.assertIn("category_percentages", summary)
        self.assertIn("subcategory_counts", summary)


if __name__ == "__main__":
    unittest.main()
