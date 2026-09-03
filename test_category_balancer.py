import unittest
from collections import Counter
from category_balancer import CategoryBalancer, TargetCategory
from categories_config import CATEGORIES_META, POSSIBLE_COMBO_MATCHES


class TestCategoryBalancer(unittest.TestCase):

    def setUp(self):
        self.all_categories = list(CATEGORIES_META.keys())

    def test_bootstrap_initialization(self):
        """
        Bootstrap Testi:
        Tüm konfigüre edilmiş kategoriler ve alt kategoriler başlangıçta 0 olarak sayaçlara eklenmelidir.
        """
        balancer = CategoryBalancer(auto_scan=False)

        # Tüm ana kategoriler mevcut ve 0 mı?
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
        # Bilim ve Popüler Kültür'ü az soruya sahip yap
        initial_counts["Bilim"] = 5
        initial_counts["Popüler Kültür"] = 5

        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts=initial_counts
        )

        self.assertFalse(balancer.is_equalized())

        # İlk seçimlerde sadece Bilim veya Popüler Kültür seçilmeli
        for _ in range(20):
            target = balancer.get_next_target(is_combo=False)
            self.assertIn(
                target.primary_category,
                ["Bilim", "Popüler Kültür"],
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
        # Bilim altındaki Genetik'i 1 yap
        initial_subs["Bilim"]["Genetik"] = 1

        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts={cat: 10 for cat in self.all_categories},
            initial_subcategory_counts=initial_subs
        )

        # Bilim kategorisi için hedef üretildiğinde alt kategori Genetik olmalı
        # Mock olarak cycle_pool'a sadece Bilim koy
        balancer.cycle_pool = ["Bilim"]
        target = balancer.get_next_target(is_combo=False)

        self.assertEqual(target.primary_category, "Bilim")
        self.assertEqual(target.target_subcategory, "Genetik")

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
        # Bilim ana kategori olacak şekilde en az yapalım
        initial_counts["Bilim"] = 2
        # Bilim'in olası eşleşmeleri: ["Tarih", "Felsefe ve Mantık", "Coğrafya", "Sanat ve Edebiyat", "Popüler Kültür"]
        # Eşleşmeler arasından Coğrafya'yı en az yapalım
        initial_counts["Coğrafya"] = 10

        balancer = CategoryBalancer(
            auto_scan=False,
            initial_category_counts=initial_counts
        )

        target = balancer.get_next_target(is_combo=True)
        self.assertEqual(target.primary_category, "Bilim")
        self.assertTrue(target.is_combo)
        # Coğrafya olası eşleşmeler içinde en az olan olduğu için seçilmeli
        self.assertEqual(target.secondary_category, "Coğrafya")

    def test_record_success_counter_updates(self):
        """
        Sayaç Güncelleme Testi:
        record_success çağrıldığında birincil, alt ve ikincil kategori sayaçları +1 artmalıdır.
        """
        balancer = CategoryBalancer(auto_scan=False)

        initial_bilim = balancer.category_counts["Bilim"]
        initial_fizik = balancer.subcategory_counts["Bilim"]["Fizik"]
        initial_tarih = balancer.category_counts["Tarih"]

        balancer.record_success(
            primary_category="Bilim",
            subcategory="Fizik",
            secondary_category="Tarih"
        )

        self.assertEqual(balancer.category_counts["Bilim"], initial_bilim + 1)
        self.assertEqual(balancer.subcategory_counts["Bilim"]["Fizik"], initial_fizik + 1)
        self.assertEqual(balancer.category_counts["Tarih"], initial_tarih + 1)

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
