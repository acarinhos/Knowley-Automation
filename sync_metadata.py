import logging
from db_manager import init_firebase
from categories_config import get_category_color, get_subcategory_image

# Loglama yapılandırması
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def sync_existing_questions_metadata():
    """
    Firestore'daki tüm mevcut soruları tarar ve categories_config.py dosyasındaki 
    güncel renk ve Firebase Storage görsel URL'leri ile senkronize eder (Batch update).
    """
    logging.info("🔄 Firestore Soru Meta Veri Senkronizasyonu Başlatılıyor...")
    
    db = init_firebase()
    questions_ref = db.collection("questions")
    docs = questions_ref.stream()

    batch = db.batch()
    updated_count = 0
    total_scanned = 0
    BATCH_LIMIT = 450  # Firestore batch limiti 500'dür, güvenli limit 450

    for doc in docs:
        total_scanned += 1
        data = doc.to_dict()
        doc_id = doc.id
        
        # Kategorileri normalize et
        categories = data.get("categories") or []
        if isinstance(categories, str):
            categories = [categories]
        elif not categories and data.get("category"):
            categories = [data.get("category")]

        # Alt kategorileri normalize et
        sub_categories = data.get("sub_categories") or []
        if isinstance(sub_categories, str):
            sub_categories = [sub_categories]
        elif not sub_categories and data.get("sub_category"):
            sub_categories = [data.get("sub_category")]
        
        # 1. Güncel Kategori Renklerini Oluştur
        updated_categories_meta = []
        for cat in categories:
            clean_cat = str(cat).strip()
            updated_categories_meta.append({
                "name": clean_cat,
                "color": get_category_color(clean_cat)
            })
            
        # 2. Güncel Alt Kategori Görsellerini Oluştur (Firebase Storage linkleri)
        updated_subcategories_meta = []
        primary_cat = categories[0] if categories else "Genel"
        for sub in sub_categories:
            clean_sub = str(sub).strip()
            updated_subcategories_meta.append({
                "name": clean_sub,
                "image_url": get_subcategory_image(primary_cat, clean_sub)
            })
            
        # Mevcut verilerle fark var mı kontrol et (Gereksiz yazmayı önler)
        current_cats_meta = data.get("categories_meta", [])
        current_subs_meta = data.get("sub_categories_meta", [])
        current_version = data.get("version")
        
        updates = {}
        if (current_cats_meta != updated_categories_meta) or (current_subs_meta != updated_subcategories_meta):
            updates["categories_meta"] = updated_categories_meta
            updates["sub_categories_meta"] = updated_subcategories_meta
            
        # Önceden oluşturulmuş sorulara versiyon numarası ekle (1.0.0)
        if not current_version:
            updates["version"] = "1.0.0"

        if updates:
            doc_ref = questions_ref.document(doc_id)
            batch.update(doc_ref, updates)
            updated_count += 1
            
            # Firestore batch sınırına ulaşıldığında yaz ve yeni batch aç
            if updated_count % BATCH_LIMIT == 0:
                batch.commit()
                logging.info(f"💾 {updated_count} adet soru güncellendi ve veritabanına yazıldı...")
                batch = db.batch()

    # Kalan son batch'i kaydet
    if updated_count % BATCH_LIMIT != 0:
        batch.commit()

    logging.info(f"🎯 Senkronizasyon Tamamlandı!")
    logging.info(f"📊 Toplam Taranan Soru: {total_scanned}")
    logging.info(f"✨ Güncellenen Soru Sayısı: {updated_count}")


if __name__ == "__main__":
    sync_existing_questions_metadata()