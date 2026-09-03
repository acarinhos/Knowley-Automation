from collections import Counter
import logging
from db_manager import init_firebase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def analyze_category_distribution():
    logging.info("🔍 Firestore soruları taranıyor...")
    
    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    category_counts = Counter()
    subcategory_counts = Counter()
    combo_count = 0
    total_questions = 0

    for doc in docs:
        total_questions += 1
        data = doc.to_dict()

        # Ana kategorileri kontrol et (liste veya tekil string olabilir)
        categories = data.get("categories") or []
        if isinstance(categories, str):
            categories = [categories]
        elif not categories and data.get("category"):
            categories = [data.get("category")]

        if len(categories) > 1:
            combo_count += 1

        for cat in categories:
            clean_cat = str(cat).strip()
            category_counts[clean_cat] += 1

        # Alt kategorileri kontrol et
        subcategories = data.get("sub_categories") or []
        if isinstance(subcategories, str):
            subcategories = [subcategories]
        elif not subcategories and data.get("sub_category"):
            subcategories = [data.get("sub_category")]

        for sub in subcategories:
            clean_sub = str(sub).strip()
            subcategory_counts[clean_sub] += 1

    # Raporlama
    print("\n" + "=" * 65)
    print(" 📊 KATEGORİ DAĞILIM RAPORU ")
    print("=" * 65)
    print(f"Toplam Soru Sayısı : {total_questions}")
    print(f"Combo (Çoklu Kategori) Soru Sayısı : {combo_count}")
    print("-" * 65)
    print(f"{'Ana Kategori':<35} | {'Soru Sayısı':<12} | {'Oran (%)':<10}")
    print("-" * 65)

    for cat, count in category_counts.most_common():
        pct = (count / total_questions * 100) if total_questions > 0 else 0
        print(f"{cat:<35} | {count:<12} | %{pct:.1f}")

    if not category_counts:
        print("Veritabanında kategori verisi bulunamadı.")

    print("=" * 65)
    print("\n" + "=" * 65)
    print(" 📑 ALT KATEGORİ (SUB-CATEGORY) DAĞILIMI (İLK 25)")
    print("=" * 65)
    print(f"{'Alt Kategori':<35} | {'Soru Sayısı':<12}")
    print("-" * 65)

    for sub, count in subcategory_counts.most_common(25):
        print(f"{sub:<35} | {count:<12}")

    print("=" * 65 + "\n")

if __name__ == "__main__":
    analyze_category_distribution()