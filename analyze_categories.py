import sys
import logging
from category_balancer import get_category_balancer
from categories_config import CATEGORIES_META

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def analyze_category_distribution():
    # Konsol UTF-8 desteği
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    logging.info("🔍 Firestore soruları CategoryBalancer ile taranıyor...")
    balancer = get_category_balancer()
    summary = balancer.get_distribution_summary()

    total_questions = summary["total_questions"]
    cat_counts = summary["category_counts"]
    cat_percentages = summary["category_percentages"]
    sub_counts = summary["subcategory_counts"]
    is_equalized = summary["is_equalized"]

    # 1. Ana Kategori Raporu
    print("\n" + "=" * 70)
    print(" 📊 ANA KATEGORİ DAĞILIM RAPORU (20 ANA KATEGORİ)")
    print("=" * 70)
    print(f"Toplam Veritabanı Soru Puanı : {total_questions}")
    print(f"Kategoriler Arası Denge Durumu : {'⚖️ TAM DENGEDE' if is_equalized else '🎯 Catch-up (Dengeleme Modu)'}")
    print("-" * 70)
    print(f"{'Ana Kategori':<30} | {'Soru Sayısı':<15} | {'Oran (%)':<10}")
    print("-" * 70)

    for cat, count in sorted(cat_counts.items(), key=lambda x: x[1], reverse=True):
        pct = cat_percentages.get(cat, 0.0)
        print(f"{cat:<30} | {count:<15} | %{pct:.1f}")

    print("=" * 70)

    # 2. Kategori Bazlı Tüm Alt Dalların Raporu
    print("\n" + "=" * 70)
    print(f" 📑 TÜM ALT DALLARIN DAĞILIMI (20 KATEGORİ)")
    print("=" * 70)

    total_configured_subs = sum(len(meta.get("sub_categories", {})) for meta in CATEGORIES_META.values())

    for cat, meta in CATEGORIES_META.items():
        cat_subs = sub_counts.get(cat, {})
        cat_total_subs = sum(cat_subs.values())
        print(f"\n📂 {cat.upper()} (Toplam Alt Dal Sorusu: {cat_total_subs} | Ana Kategori Soru: {cat_counts.get(cat, 0)})")
        print("-" * 70)
        print(f"  {'Alt Dal (Sub-Category)':<35} | {'Soru Sayısı':<12}")
        print("  " + "-" * 50)

        # En azdan en çoğa sırala (böylece eksik kalanlar hemen görülür)
        for sub_name in meta.get("sub_categories", {}).keys():
            count = cat_subs.get(sub_name, 0)
            print(f"  {sub_name:<35} | {count:<12}")

    print("\n" + "=" * 70)
    print(f"✅ Toplam {len(CATEGORIES_META)} Ana Kategori ve {total_configured_subs} Alt Dalın Tamamı İzlendi.")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    analyze_category_distribution()